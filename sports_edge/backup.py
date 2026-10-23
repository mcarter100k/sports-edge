"""Nightly encrypted off-machine backup and restore (docs/specs/backup_spec.md).

    python -m sports_edge.backup run
    python -m sports_edge.backup restore <archive.tar.gz.enc> <empty folder>
    python -m sports_edge.backup fetch <archive name> <folder>

Reads evidence only: the database through a read-only export under the jobs'
lock, CSV ledgers under a shared flock, logs as files.
"""
import base64
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

from . import config, net
from .store import Store
from .timeutil import iso_z, utcnow

OPENSSL = "/usr/bin/openssl"
CIPHER = ["-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-md", "sha256"]
MIN_PASSPHRASE = 16
KEEP_LOCAL = 7
B2_AUTH = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"
SUFFIX = ".tar.gz.enc"


class BackupError(Exception):
    pass


# Paths and settings ------------------------------------------------------------

def backup_dir() -> Path:
    value = os.environ.get("BACKUP_DIR")
    return Path(value) if value else Path.home() / "sports-edge-backups"


def research_datasets() -> Path:
    value = os.environ.get("SPORTS_EDGE_RESEARCH_DIR")
    return (Path(value) if value else Path.home() / "sports-edge-research") / "datasets"


def passphrase():
    p = os.environ.get("BACKUP_PASSPHRASE") or ""
    if len(p) < MIN_PASSPHRASE:
        raise BackupError(f"BACKUP_PASSPHRASE is missing or shorter than {MIN_PASSPHRASE} characters")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Collect --------------------------------------------------------------------------

def export_database(stage):
    """EXPORT DATABASE under the jobs' lock. Returns {table: rows} read in the same session."""
    db = config.db_path()
    if not db.exists():
        raise BackupError(f"database {db} does not exist")
    out = stage / "db"
    with Store.open(db, read_only=True) as store:
        tables = [t for (t,) in store.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1").fetchall()]
        counts = {t: store.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
        store.con.execute(f"EXPORT DATABASE '{out}' (FORMAT parquet)")
    return counts


def copy_ledgers(stage):
    """Every data/*.csv, each read under a shared flock so no half-appended row is copied."""
    copied = []
    dest = stage / "data"
    for src in sorted(config.data_dir().glob("*.csv")):
        dest.mkdir(parents=True, exist_ok=True)
        with open(src, "rb") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                data = fh.read()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        (dest / src.name).write_bytes(data)
        copied.append(src.name)
    return copied


def copy_tree(src, dest):
    if src.exists():
        shutil.copytree(src, dest, symlinks=False)


def manifest(stage, counts):
    files = {}
    for p in sorted(stage.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            rel = str(p.relative_to(stage))
            files[rel] = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
    m = {"created_utc": iso_z(utcnow()), "duckdb_version": duckdb.__version__, "tables": counts, "files": files}
    (stage / "manifest.json").write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")
    return m


def make_tar(stage, path):
    with tarfile.open(path, "w:gz") as tar:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=str(p.relative_to(stage)), recursive=False)


def openssl(args, env_pass):
    proc = subprocess.run([os.environ.get("BACKUP_OPENSSL", OPENSSL), "enc", *args],
                          env={**os.environ, "BACKUP_PASSPHRASE": env_pass}, capture_output=True)
    if proc.returncode != 0:
        raise BackupError(f"openssl enc failed (exit {proc.returncode}): {proc.stderr.decode()[:200].strip()}")


def encrypt(src, dest, pw):
    openssl([*CIPHER, "-salt", "-pass", "env:BACKUP_PASSPHRASE", "-in", str(src), "-out", str(dest)], pw)


def decrypt(src, dest, pw):
    openssl(["-d", *CIPHER, "-pass", "env:BACKUP_PASSPHRASE", "-in", str(src), "-out", str(dest)], pw)


# Upload ---------------------------------------------------------------------------

def https_request(url, method="GET", headers=None, body=None, timeout=120):
    """(status, headers, body). Certificate verification on (net.ssl_context)."""
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=net.ssl_context()) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


class B2:
    """Backblaze B2 native API: authorize, upload, download. transport(url, method, headers, body)."""

    def __init__(self, key_id, app_key, transport=https_request):
        if not key_id or not app_key:
            raise BackupError("B2 key id or application key missing")
        self.transport = transport
        token = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
        status, _h, body = transport(B2_AUTH, "GET", {"Authorization": f"Basic {token}"}, None)
        if status != 200:
            raise BackupError(f"B2 authorize failed: HTTP {status}: {body[:200]!r}")
        auth = json.loads(body)
        storage = auth["apiInfo"]["storageApi"]
        self.api_url, self.download_url = storage["apiUrl"], storage["downloadUrl"]
        self.token = auth["authorizationToken"]
        self.allowed_bucket = (storage.get("allowed") or {}).get("buckets") or []

    def _post(self, op, payload):
        status, _h, body = self.transport(f"{self.api_url}/b2api/v3/{op}", "POST",
                                          {"Authorization": self.token, "Content-Type": "application/json"},
                                          json.dumps(payload).encode())
        if status != 200:
            raise BackupError(f"B2 {op} failed: HTTP {status}: {body[:200]!r}")
        return json.loads(body)

    def upload(self, bucket_id, path, name):
        target = self._post("b2_get_upload_url", {"bucketId": bucket_id})
        sha1 = sha1_file(path)
        data = Path(path).read_bytes()
        status, _h, body = self.transport(target["uploadUrl"], "POST", {
            "Authorization": target["authorizationToken"], "X-Bz-File-Name": urllib.parse.quote(name),
            "Content-Type": "b2/x-auto", "Content-Length": str(len(data)), "X-Bz-Content-Sha1": sha1}, data)
        if status != 200:
            raise BackupError(f"B2 upload of {name} failed: HTTP {status}: {body[:200]!r}")
        returned = json.loads(body).get("contentSha1")
        if returned != sha1:
            raise BackupError(f"B2 stored {name} with SHA-1 {returned}, local is {sha1}")
        return sha1

    def download(self, bucket_name, name, dest):
        url = f"{self.download_url}/file/{urllib.parse.quote(bucket_name)}/{urllib.parse.quote(name)}"
        status, _h, body = self.transport(url, "GET", {"Authorization": self.token}, None)
        if status != 200:
            raise BackupError(f"B2 download of {name} failed: HTTP {status}")
        Path(dest).write_bytes(body)


def upload(archive, sidecar, target=None, b2_transport=https_request):
    target = target or os.environ.get("BACKUP_TARGET") or "b2"
    if target.startswith("dir:"):
        folder = Path(target[4:])
        folder.mkdir(parents=True, exist_ok=True)
        for f in (archive, sidecar):
            shutil.copy2(f, folder / f.name)
            if sha256_file(folder / f.name) != sha256_file(f):
                raise BackupError(f"copy of {f.name} to {folder} differs from the original")
        return target
    if target != "b2":
        raise BackupError(f"BACKUP_TARGET must be b2 or dir:<path>, got {target!r}")
    bucket = os.environ.get("BACKUP_B2_BUCKET_ID")
    if not (bucket and os.environ.get("BACKUP_B2_KEY_ID") and os.environ.get("BACKUP_B2_APP_KEY")):
        raise BackupError(f"upload not configured: set BACKUP_B2_KEY_ID, BACKUP_B2_APP_KEY and "
                          f"BACKUP_B2_BUCKET_ID in .env (local archive kept at {archive})")
    b2 = B2(os.environ["BACKUP_B2_KEY_ID"], os.environ["BACKUP_B2_APP_KEY"], b2_transport)
    for f in (archive, sidecar):
        b2.upload(bucket, f, f.name)
    return "b2"


# Run ----------------------------------------------------------------------------------

def prune(folder, keep=KEEP_LOCAL):
    archives = sorted(folder.glob(f"sports-edge-*{SUFFIX}"))
    removed = []
    for a in archives[:-keep] if len(archives) > keep else []:
        a.unlink()
        side = a.with_name(a.name + ".sha256")
        if side.exists():
            side.unlink()
        removed.append(a.name)
    return removed


def run(now=None, b2_transport=https_request):
    pw = passphrase()
    now = now or utcnow()
    name = f"sports-edge-{now.strftime('%Y%m%dT%H%M%SZ')}{SUFFIX}"
    out_dir = backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    work = Path(tempfile.mkdtemp(prefix="sports-edge-backup-"))
    try:
        stage = work / "stage"
        stage.mkdir()
        counts = export_database(stage)
        copy_ledgers(stage)
        copy_tree(config.logs_dir(), stage / "logs")
        copy_tree(research_datasets(), stage / "research_datasets")
        m = manifest(stage, counts)
        tar = work / "archive.tar.gz"
        make_tar(stage, tar)
        archive = out_dir / name
        encrypt(tar, archive, pw)
        digest = sha256_file(archive)
        sidecar = out_dir / f"{name}.sha256"
        sidecar.write_text(f"{digest}  {name}\n")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    uploaded = upload(archive, sidecar, b2_transport=b2_transport)
    pruned = prune(out_dir)
    return (f"BACKUP OK name={name} bytes={archive.stat().st_size} sha256={digest} tables={len(counts)} "
            f"rows={sum(counts.values())} files={len(m['files'])} uploaded={uploaded} pruned={len(pruned)}")


def restore(archive, dest):
    archive, dest = Path(archive), Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BackupError(f"{dest} is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    sidecar = archive.with_name(archive.name + ".sha256")
    if not sidecar.exists():
        raise BackupError(f"sidecar {sidecar.name} is missing")
    expected = sidecar.read_text().split()[0]
    if sha256_file(archive) != expected:
        raise BackupError("archive sha256 differs from its sidecar")
    work = Path(tempfile.mkdtemp(prefix="sports-edge-restore-"))
    try:
        tar = work / "archive.tar.gz"
        decrypt(archive, tar, passphrase())
        with tarfile.open(tar, "r:gz") as t:
            t.extractall(dest, filter="data")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m = json.loads((dest / "manifest.json").read_text())
    for rel, e in m["files"].items():
        p = dest / rel
        if not p.exists():
            raise BackupError(f"restored file {rel} is missing")
        if sha256_file(p) != e["sha256"]:
            raise BackupError(f"restored file {rel} differs from the manifest")
    db = dest / "sports_edge.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute(f"IMPORT DATABASE '{dest / 'db'}'")
        got = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in m["tables"]}
    finally:
        con.close()
    for t, n in m["tables"].items():
        if got[t] != n:
            raise BackupError(f"table {t}: restored {got[t]} rows, manifest says {n}")
    return (f"RESTORE OK files={len(m['files'])} tables={len(got)} rows={sum(got.values())} "
            f"created={m['created_utc']} db={db}")


def fetch(name, folder, transport=https_request):
    key_id, app_key = os.environ.get("BACKUP_B2_RESTORE_KEY_ID"), os.environ.get("BACKUP_B2_RESTORE_APP_KEY")
    bucket = os.environ.get("BACKUP_B2_BUCKET_NAME")
    if not bucket:
        raise BackupError("BACKUP_B2_BUCKET_NAME is required to download")
    b2 = B2(key_id, app_key, transport)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for n in (name, f"{name}.sha256"):
        b2.download(bucket, n, folder / n)
    return f"FETCH OK {folder / name}"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv     # .env is loaded on package import
    try:
        if argv[:1] == ["run"]:
            print(run())
        elif argv[:1] == ["restore"] and len(argv) == 3:
            print(restore(argv[1], argv[2]))
        elif argv[:1] == ["fetch"] and len(argv) == 3:
            print(fetch(argv[1], argv[2]))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except BackupError as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
