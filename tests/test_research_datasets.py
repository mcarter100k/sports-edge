"""Frozen datasets (docs/specs/research_spec.md)."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research import datasets
from sports_edge import config
from sports_edge.store import Store

REPO = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def seed_db(rows=3, settlement=True):
    with Store.open() as s:
        s.append("price_snapshots", [
            {"run_id": f"r{i}", "ts_utc": T0, "tier": "near", "ticker": f"T{i}", "status": "active",
             "yes_bid": "0.40", "yes_ask": "0.42"} for i in range(rows)])
        if settlement:
            s.append("settlements", [{"ticker": "T0", "result": "yes", "settlement_value": "1",
                                      "settlement_ts": T0, "recorded_utc": T0, "source": "scan"}])


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_research_dir_follows_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SPORTS_EDGE_RESEARCH_DIR", str(tmp_path / "r"))
    assert datasets.research_dir() == tmp_path / "r"


def test_export_is_reproducible_and_leaves_the_database_unchanged(tmp_path):
    seed_db()
    db_before = sha(config.db_path())
    a = datasets.export_live("live", tables=["price_snapshots", "settlements"], root=tmp_path / "r")
    b = datasets.export_live("live", tables=["price_snapshots", "settlements"], root=tmp_path / "r2")
    assert sha(config.db_path()) == db_before
    assert a.name == b.name and re.fullmatch(r"[0-9a-f]{16}", a.name)
    for f in ("price_snapshots.parquet", "settlements.parquet"):
        assert sha(a / f) == sha(b / f), "Parquet bytes must be identical for identical data"
    m = datasets.read_manifest(a)
    assert m["tables"]["price_snapshots"]["rows"] == 3
    assert m["dataset_id"] == a.name


def test_same_rows_in_a_different_insert_order_get_the_same_id(tmp_path):
    import duckdb
    con = duckdb.connect()
    p1, p2 = tmp_path / "a.parquet", tmp_path / "b.parquet"
    con.execute(f"COPY (SELECT * FROM (VALUES (2,'b'),(1,'a')) t(x,y)) TO '{p1}' (FORMAT parquet)")
    con.execute(f"COPY (SELECT * FROM (VALUES (1,'a'),(2,'b')) t(x,y)) TO '{p2}' (FORMAT parquet)")
    assert datasets.freeze("t", {"t": p1}, root=tmp_path / "r").name == \
        datasets.freeze("t", {"t": p2}, root=tmp_path / "r2").name


def test_changed_data_gets_a_new_id(tmp_path):
    seed_db(3)
    a = datasets.export_live("live", tables=["price_snapshots"], root=tmp_path / "r")
    seed_db(1, settlement=False)
    b = datasets.export_live("live", tables=["price_snapshots"], root=tmp_path / "r")
    assert a.name != b.name and a.exists() and b.exists()


def test_refreezing_the_same_data_verifies_and_keeps_the_folder(tmp_path):
    seed_db()
    a = datasets.export_live("live", tables=["settlements"], root=tmp_path / "r")
    before = sha(a / "manifest.json")
    again = datasets.export_live("live", tables=["settlements"], root=tmp_path / "r")
    assert again == a and sha(a / "manifest.json") == before


def test_open_dataset_refuses_a_tampered_file(tmp_path):
    seed_db()
    a = datasets.export_live("live", tables=["price_snapshots"], root=tmp_path / "r")
    con = datasets.open_dataset(a)
    assert con.execute("SELECT count(*) FROM price_snapshots").fetchone()[0] == 3
    con.close()
    p = a / "price_snapshots.parquet"
    data = bytearray(p.read_bytes())
    data[len(data) // 2] ^= 0xFF
    p.write_bytes(bytes(data))
    with pytest.raises(datasets.DatasetMismatch, match="sha256"):
        datasets.open_dataset(a)


def test_open_dataset_refuses_an_edited_manifest(tmp_path):
    seed_db()
    a = datasets.export_live("live", tables=["price_snapshots"], root=tmp_path / "r")
    m = json.loads((a / "manifest.json").read_text())
    m["tables"]["price_snapshots"]["rows"] = 4
    (a / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(datasets.DatasetMismatch):
        datasets.verify(a)


def test_export_fails_on_an_unknown_table(tmp_path):
    seed_db()
    with pytest.raises(datasets.DatasetMismatch, match="nope"):
        datasets.export_live("live", tables=["nope"], root=tmp_path / "r")


def test_live_package_never_imports_research():
    for path in (REPO / "sports_edge").rglob("*.py"):
        text = path.read_text()
        assert not re.search(r"^\s*(from|import)\s+research\b", text, re.M), path
