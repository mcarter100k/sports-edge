"""Frozen datasets (docs/specs/research_spec.md).

A dataset is a folder of Parquet files plus manifest.json. Its id comes from
the rows themselves, so the same data always gets the same id, and
open_dataset() refuses a folder whose files no longer match the manifest.
"""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import duckdb

from sports_edge import config
from sports_edge.store import TABLES, Store
from sports_edge.timeutil import iso_z, utcnow

MANIFEST = "manifest.json"
ID_LEN = 16


class DatasetMismatch(Exception):
    pass


def research_dir() -> Path:
    value = os.environ.get("SPORTS_EDGE_RESEARCH_DIR")
    return Path(value) if value else Path.home() / "sports-edge-research"


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _columns(con, source):
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]


def content_sha256(con, source):
    """sha256 over the rows of `source` in a fixed order (every column ascending), with column
    names first. Independent of the Parquet writer."""
    cols = _columns(con, source)
    order = ", ".join(f'"{c}" ASC NULLS FIRST' for c in cols)
    h = hashlib.sha256(json.dumps(cols).encode())
    cur = con.execute(f"SELECT * FROM {source} ORDER BY {order}")
    while True:
        rows = cur.fetchmany(10000)
        if not rows:
            break
        for row in rows:
            h.update(json.dumps(row, default=str, separators=(",", ":")).encode())
            h.update(b"\n")
    return h.hexdigest()


def write_parquet(con, source, path):
    """Copy `source` to Parquet with rows in a fixed order, one thread."""
    cols = _columns(con, source)
    order = ", ".join(f'"{c}" ASC NULLS FIRST' for c in cols)
    con.execute("SET threads TO 1")
    con.execute(f"COPY (SELECT * FROM {source} ORDER BY {order}) TO '{path}' (FORMAT parquet, COMPRESSION zstd)")


def dataset_id(entries):
    canon = json.dumps({t: [e["rows"], e["content_sha256"]] for t, e in sorted(entries.items())},
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:ID_LEN]


def freeze(name, tables, root=None, source=None):
    """tables: {table: parquet path}. Writes datasets/<name>/<id>/ and returns that folder.
    An existing folder with the same id is verified, never overwritten."""
    root = Path(root) if root else research_dir()
    con = duckdb.connect()
    entries = {}
    try:
        for table, path in sorted(tables.items()):
            src = f"read_parquet('{path}')"
            (rows,) = con.execute(f"SELECT count(*) FROM {src}").fetchone()
            entries[table] = {"rows": rows, "content_sha256": content_sha256(con, src)}
        did = dataset_id(entries)
        final = root / "datasets" / name / did
        if final.exists():
            open_dataset(final).close()
            return final
        stage = Path(tempfile.mkdtemp(prefix=f".{did}-", dir=_ensure(root / "datasets" / name)))
        for table, path in sorted(tables.items()):
            out = stage / f"{table}.parquet"
            write_parquet(con, f"read_parquet('{path}')", out)
            entries[table]["file"] = out.name
            entries[table]["file_sha256"] = file_sha256(out)
    finally:
        con.close()
    manifest = {"dataset_id": did, "name": name, "frozen_utc": iso_z(utcnow()), "source": source or {},
                "duckdb_version": duckdb.__version__, "tables": entries}
    (stage / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    os.rename(stage, final)
    return final


def _ensure(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_live(name, tables=None, db=None, root=None):
    """Freeze tables of the live database, read under the scheduled jobs' lock."""
    tables = list(tables or TABLES)
    db = Path(db) if db else config.db_path()
    stage = Path(tempfile.mkdtemp(prefix="export-"))
    try:
        with Store.open(db, read_only=True) as store:
            store.con.execute("SET threads TO 1")
            present = {r[0] for r in store.con.execute(
                "SELECT table_name FROM information_schema.tables").fetchall()}
            missing = [t for t in tables if t not in present]
            if missing:
                raise DatasetMismatch(f"tables not in {db}: {missing}")
            paths = {}
            for t in tables:
                paths[t] = stage / f"{t}.parquet"
                write_parquet(store.con, t, paths[t])
        return freeze(name, paths, root=root, source={"kind": "live_db", "db": str(db)})
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def read_manifest(folder):
    return json.loads((Path(folder) / MANIFEST).read_text())


def verify(folder):
    """Raise DatasetMismatch unless every file matches the manifest and the id matches the rows."""
    folder = Path(folder)
    manifest = read_manifest(folder)
    con = duckdb.connect()
    try:
        for table, e in manifest["tables"].items():
            path = folder / e["file"]
            if not path.exists():
                raise DatasetMismatch(f"{table}: {e['file']} is missing")
            if file_sha256(path) != e["file_sha256"]:
                raise DatasetMismatch(f"{table}: file sha256 differs from the manifest")
            src = f"read_parquet('{path}')"
            (rows,) = con.execute(f"SELECT count(*) FROM {src}").fetchone()
            if rows != e["rows"]:
                raise DatasetMismatch(f"{table}: {rows} rows, manifest says {e['rows']}")
    finally:
        con.close()
    if dataset_id(manifest["tables"]) != manifest["dataset_id"] or folder.name != manifest["dataset_id"]:
        raise DatasetMismatch("dataset id does not match its contents or folder name")
    return manifest


def open_dataset(folder):
    """A DuckDB connection with one view per table, after verify()."""
    folder = Path(folder)
    manifest = verify(folder)
    con = duckdb.connect()
    for table, e in manifest["tables"].items():
        con.execute(f"CREATE VIEW \"{table}\" AS SELECT * FROM read_parquet('{folder / e['file']}')")
    return con
