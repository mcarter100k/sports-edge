"""Test isolation (AGENTS.md rule 4). Never weaken this to make a test pass.

- Every test runs with its own data, log and state directories.
- Unit tests cannot reach the network: an interpreter audit hook refuses
  socket connects and DNS lookups unless the test is marked integration.
- The session FAILS if any file in the repo was created, changed or
  deleted (venv/, .git, caches excepted).
"""
import hashlib
import os
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"venv", ".git", "__pycache__", ".pytest_cache"}

_network = {"allowed": False}


def _audit(event, args):
    if _network["allowed"]:
        return
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        target = args[1] if event == "socket.connect" and len(args) > 1 else args[0] if args else ""
        if event == "socket.connect" and isinstance(target, str):  # AF_UNIX paths are local
            return
        host = target[0] if isinstance(target, tuple) else target
        if host in ("localhost", "127.0.0.1", "::1", None):
            return
        raise PermissionError(f"network blocked in unit tests: {event} {target!r}")


sys.addaudithook(_audit)


def repo_hashes():
    out = {}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".pyc"):
                continue
            p = Path(root) / name
            out[str(p.relative_to(REPO))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: test may use the real network")
    config._repo_before = repo_hashes()


def pytest_sessionfinish(session, exitstatus):
    before = session.config._repo_before
    after = repo_hashes()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        print("\nREPO CHANGED DURING TESTS (isolation failure):", file=sys.stderr)
        for k in changed:
            print(f"  {k}", file=sys.stderr)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, request):
    for sub in ("data", "logs", "state"):
        (tmp_path / sub).mkdir()
    monkeypatch.setenv("SPORTS_EDGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SPORTS_EDGE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SPORTS_EDGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SPORTS_EDGE_NOTIFY", "log")
    for var in ("ODDS_API_KEY", "KALSHI_BASE_URL", "SHADOW_EDGE_THRESHOLD", "SHADOW_CONTRACTS",
                "FEE_PRECISION", "ODDS_MAX_AGE_MIN", "ODDS_DAILY_CREDITS", "ODDS_INTERVAL_MIN",
                "ODDS_HORIZON_H", "SSL_CERT_FILE", "KALSHI_TICKER_BATCH"):
        monkeypatch.delenv(var, raising=False)
    # Fees fail closed unless a test builds its own table (helpers.fee_table).
    monkeypatch.setenv("SPORTS_EDGE_FEE_TABLE", str(tmp_path / "no_fee_table.json"))
    monkeypatch.chdir(tmp_path)
    _network["allowed"] = request.node.get_closest_marker("integration") is not None
    yield tmp_path
    _network["allowed"] = False


def network_probe():
    """Used by the positive-control test: must raise in a unit test."""
    return socket.getaddrinfo("external-api.kalshi.com", 443)
