"""Nightly encrypted backup and restore (docs/specs/backup_spec.md)."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports_edge import backup, config, ledger
from sports_edge.store import Store

REPO = Path(__file__).resolve().parent.parent
T0 = datetime(2026, 10, 22, 11, 15, tzinfo=timezone.utc)
PW = "correct horse battery staple"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_PASSPHRASE", PW)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("BACKUP_TARGET", f"dir:{tmp_path / 'offsite'}")
    with Store.open() as s:
        s.append("price_snapshots", [{"run_id": f"r{i}", "ts_utc": T0, "tier": "near", "ticker": f"T{i}",
                                      "status": "active", "yes_bid": "0.40", "yes_ask": "0.42"} for i in range(5)])
        s.append("settlements", [{"ticker": "T0", "result": "yes", "settlement_value": "1", "settlement_ts": T0,
                                  "recorded_utc": T0, "source": "scan"}])
    ledger.append_rows(config.data_dir() / "shadow_ledger.csv", ["row_id", "edge"],
                       [{"row_id": "a", "edge": "0.031"}])
    (config.logs_dir() / "snapshot_2026-10-22.log").write_text("SNAPSHOT OK\n")
    ds = tmp_path / "research" / "datasets" / "live" / "abc"
    ds.mkdir(parents=True)
    (ds / "manifest.json").write_text("{}\n")
    return tmp_path


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def only_archive(folder):
    (a,) = sorted(Path(folder).glob("sports-edge-*.tar.gz.enc"))
    return a


def test_round_trip_restores_every_file_and_row(env):
    db_before = sha(config.db_path())
    line = backup.run(now=T0)
    assert line.startswith("BACKUP OK name=sports-edge-20261022T111500Z.tar.gz.enc")
    assert "uploaded=dir:" in line
    assert sha(config.db_path()) == db_before, "the live database must not change"
    archive = only_archive(env / "backups")
    assert sha(archive) == sha(env / "offsite" / archive.name)
    out = env / "restored"
    result = backup.restore(env / "offsite" / archive.name, out)
    assert result.startswith("RESTORE OK")
    assert (out / "data" / "shadow_ledger.csv").read_bytes() == (config.data_dir() / "shadow_ledger.csv").read_bytes()
    assert (out / "logs" / "snapshot_2026-10-22.log").read_text() == "SNAPSHOT OK\n"
    assert (out / "research_datasets" / "live" / "abc" / "manifest.json").exists()
    import duckdb
    con = duckdb.connect(str(out / "sports_edge.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM price_snapshots").fetchone()[0] == 5
    assert con.execute("SELECT result FROM settlements").fetchone()[0] == "yes"
    con.close()


def test_archive_is_encrypted(env):
    backup.run(now=T0)
    data = only_archive(env / "backups").read_bytes()
    assert data.startswith(b"Salted__")
    assert b"shadow_ledger" not in data and b"SNAPSHOT OK" not in data


def test_tampered_archive_fails_restore(env):
    backup.run(now=T0)
    a = only_archive(env / "backups")
    raw = bytearray(a.read_bytes())
    raw[-40] ^= 0x01
    a.write_bytes(bytes(raw))
    with pytest.raises(backup.BackupError, match="sidecar"):
        backup.restore(a, env / "out")


def test_wrong_passphrase_fails_restore(env, monkeypatch):
    backup.run(now=T0)
    monkeypatch.setenv("BACKUP_PASSPHRASE", "a different passphrase entirely")
    with pytest.raises(backup.BackupError, match="openssl"):
        backup.restore(only_archive(env / "backups"), env / "out")


def test_edited_restored_file_is_caught(env, monkeypatch):
    backup.run(now=T0)
    real = backup.tarfile.TarFile.extractall

    def tamper(self, path, *a, **kw):
        real(self, path, *a, **kw)
        (Path(path) / "logs" / "snapshot_2026-10-22.log").write_text("edited\n")

    monkeypatch.setattr(backup.tarfile.TarFile, "extractall", tamper)
    with pytest.raises(backup.BackupError, match="differs from the manifest"):
        backup.restore(only_archive(env / "backups"), env / "out")


@pytest.mark.parametrize("value", [None, "short"])
def test_missing_or_short_passphrase_fails_before_anything_is_written(env, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("BACKUP_PASSPHRASE")
    else:
        monkeypatch.setenv("BACKUP_PASSPHRASE", value)
    with pytest.raises(backup.BackupError, match="BACKUP_PASSPHRASE"):
        backup.run(now=T0)
    assert not (env / "backups").exists()


def test_b2_not_configured_fails_but_keeps_the_local_archive(env, monkeypatch):
    monkeypatch.setenv("BACKUP_TARGET", "b2")
    for k in ("BACKUP_B2_KEY_ID", "BACKUP_B2_APP_KEY", "BACKUP_B2_BUCKET_ID"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(backup.BackupError, match="upload not configured"):
        backup.run(now=T0)
    assert only_archive(env / "backups").exists()


class FakeB2:
    def __init__(self, corrupt=False):
        self.calls, self.stored, self.corrupt = [], {}, corrupt

    def __call__(self, url, method, headers, body):
        self.calls.append((url, method, dict(headers)))
        if url == backup.B2_AUTH:
            assert headers["Authorization"].startswith("Basic ")
            return 200, {}, json.dumps({"authorizationToken": "TOK", "apiInfo": {"storageApi": {
                "apiUrl": "https://api.example", "downloadUrl": "https://dl.example"}}}).encode()
        if url.endswith("/b2_get_upload_url"):
            assert json.loads(body) == {"bucketId": "B1"} and headers["Authorization"] == "TOK"
            return 200, {}, json.dumps({"uploadUrl": "https://up.example/x", "authorizationToken": "UTOK"}).encode()
        if url == "https://up.example/x":
            sha1 = hashlib.sha1(body).hexdigest()
            assert headers["X-Bz-Content-Sha1"] == sha1 and headers["Authorization"] == "UTOK"
            self.stored[headers["X-Bz-File-Name"]] = body
            return 200, {}, json.dumps({"contentSha1": "0" * 40 if self.corrupt else sha1}).encode()
        if url.startswith("https://dl.example/file/bucket/"):
            name = url.rsplit("/", 1)[1]
            return 200, {}, self.stored[name]
        return 404, {}, b""


def b2_env(monkeypatch):
    monkeypatch.setenv("BACKUP_TARGET", "b2")
    monkeypatch.setenv("BACKUP_B2_KEY_ID", "kid")
    monkeypatch.setenv("BACKUP_B2_APP_KEY", "key")
    monkeypatch.setenv("BACKUP_B2_BUCKET_ID", "B1")


def test_b2_upload_sends_sha1_and_checks_the_reply_then_fetch_restores(env, monkeypatch):
    b2_env(monkeypatch)
    fake = FakeB2()
    line = backup.run(now=T0, b2_transport=fake)
    assert "uploaded=b2" in line
    name = only_archive(env / "backups").name
    assert set(fake.stored) == {name, f"{name}.sha256"}
    monkeypatch.setenv("BACKUP_B2_RESTORE_KEY_ID", "rk")
    monkeypatch.setenv("BACKUP_B2_RESTORE_APP_KEY", "rkey")
    monkeypatch.setenv("BACKUP_B2_BUCKET_NAME", "bucket")
    assert backup.fetch(name, env / "fetched", transport=fake).startswith("FETCH OK")
    assert backup.restore(env / "fetched" / name, env / "out").startswith("RESTORE OK")


def test_b2_sha1_mismatch_fails(env, monkeypatch):
    b2_env(monkeypatch)
    with pytest.raises(backup.BackupError, match="SHA-1"):
        backup.run(now=T0, b2_transport=FakeB2(corrupt=True))


def test_b2_authorize_failure_fails(env, monkeypatch):
    b2_env(monkeypatch)
    with pytest.raises(backup.BackupError, match="authorize failed"):
        backup.run(now=T0, b2_transport=lambda url, m, h, b: (401, {}, b"bad key"))


def test_retention_keeps_the_newest_seven_local_archives(env):
    for i in range(9):
        backup.run(now=T0 + timedelta(days=i))
    names = sorted(p.name for p in (env / "backups").glob("*.tar.gz.enc"))
    assert len(names) == 7 and names[0].startswith("sports-edge-20261024")
    assert len(list((env / "backups").glob("*.sha256"))) == 7
    assert len(list((env / "offsite").glob("*.tar.gz.enc"))) == 9, "the off-machine copies are never pruned"


def test_restore_refuses_a_non_empty_folder(env):
    backup.run(now=T0)
    (env / "busy").mkdir()
    (env / "busy" / "x").write_text("x")
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore(only_archive(env / "backups"), env / "busy")


def test_missing_database_fails(env):
    config.db_path().unlink()
    with pytest.raises(backup.BackupError, match="does not exist"):
        backup.run(now=T0)


def run_wrapper(env, extra):
    e = {**os.environ, "SPORTS_EDGE_PROJ": str(REPO), "SPORTS_EDGE_PYTHON": sys.executable,
         "PYTHONDONTWRITEBYTECODE": "1", **extra}
    return subprocess.run(["/bin/bash", str(REPO / "scripts" / "run_backup.sh")], env=e, capture_output=True,
                          text=True, timeout=120)


def test_wrapper_alerts_loudly_on_failure_and_records_success(env, monkeypatch):
    proc = run_wrapper(env, {"BACKUP_PASSPHRASE": "short"})
    assert proc.returncode != 0
    alerts = (config.logs_dir() / "alerts.log").read_text()
    assert "ALERT logged-only: backup failed" in alerts and "BACKUP_PASSPHRASE" in alerts
    proc = run_wrapper(env, {})
    assert proc.returncode == 0, proc.stderr
    log = next(config.logs_dir().glob("backup_*.log")).read_text()
    assert "BACKUP OK" in log
    assert "RECOVERED" in (config.logs_dir() / "alerts.log").read_text()


def test_backup_leaves_every_file_under_data_unchanged(env):
    before = {p.name: sha(p) for p in config.data_dir().iterdir() if p.is_file()}
    backup.run(now=T0)
    after = {p.name: sha(p) for p in config.data_dir().iterdir() if p.is_file()}
    assert after == before


def test_restored_row_counts_must_match_the_manifest(env, monkeypatch):
    backup.run(now=T0)
    real = backup.tarfile.TarFile.extractall

    def edit_counts(self, path, *a, **kw):
        real(self, path, *a, **kw)
        m = json.loads((Path(path) / "manifest.json").read_text())
        m["tables"]["price_snapshots"] += 1
        (Path(path) / "manifest.json").write_text(json.dumps(m))

    monkeypatch.setattr(backup.tarfile.TarFile, "extractall", edit_counts)
    with pytest.raises(backup.BackupError, match="table price_snapshots: restored 5 rows, manifest says 6"):
        backup.restore(only_archive(env / "backups"), env / "out")


def test_ledgers_are_read_under_a_shared_lock(env, monkeypatch):
    calls = []
    real = backup.fcntl.flock
    monkeypatch.setattr(backup.fcntl, "flock", lambda fd, op: (calls.append(op), real(fd, op))[1])
    backup.run(now=T0)
    assert backup.fcntl.LOCK_SH in calls and backup.fcntl.LOCK_UN in calls


def test_database_is_opened_read_only_under_the_lock(env, monkeypatch):
    seen = []
    real = backup.Store.open

    def spy(*a, **kw):
        seen.append(kw.get("read_only"))
        return real(*a, **kw)

    monkeypatch.setattr(backup.Store, "open", spy)
    backup.run(now=T0)
    assert seen == [True]
