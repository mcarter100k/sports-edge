"""Gate 2: run real code against the real APIs with every side effect neutralized.

    venv/bin/python scripts/reality_run.py [--code DIR] [--keep] [--with-live-data] -- <command...>

The command runs from a temporary directory with SPORTS_EDGE_DATA_DIR,
SPORTS_EDGE_LOG_DIR, SPORTS_EDGE_STATE_DIR and SPORTS_EDGE_SHEET_DIR pointing inside it,
and SPORTS_EDGE_NOTIFY=log (no notification or iMessage is sent). Credentials come from the live
repo's .env. Every file under data/, logs/ and state/ of both the live repo and the
code checkout, and the live iCloud sheet folder, is hashed before and after; any
change fails the run (exit 2). The output is
printed in full and scanned for failure markers: an exit code alone is not
a certification (exit 3 on a marker). With --code other than the live
checkout, the wrappers run that checkout too (docs/specs/reality_gate_spec.md).

Exit codes: 0 pass, 1 the command failed, 2 the live data changed, 3 failure marker in output.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LIVE = Path("/Users/quis/sports-edge")
WATCHED = ("data", "logs", "state")
SHEET = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "sports-edge"
MARKERS = ("Traceback (most recent call last)", "SCAN EMPTY", "KalshiError", "OddsError",
           "StoreLocked", "LedgerSchemaMismatch", "ALERT DELIVERY FAILED", "SLATE FAILED")


def snapshot_hashes(roots):
    """sha256 of every file under data/, logs/ and state/ of each root, and the live sheet folder."""
    hashes = {}
    for root in roots:
        for base in [root / sub for sub in WATCHED] + ([SHEET] if root == LIVE else []):
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def code_env(code):
    """Settings common.sh reads, so a wrapper runs --code's checkout instead of changing into the live one.
    Worktrees have no venv/ or .env; the live .env is already in the environment."""
    if code == LIVE:
        return {}
    return {"SPORTS_EDGE_PROJ": str(code), "SPORTS_EDGE_PYTHON": str(LIVE / "venv" / "bin" / "python"),
            "SPORTS_EDGE_NO_ENV_FILE": "1"}


def run_env(code, tmp):
    """The command's environment: live credentials, every side effect pointed into tmp."""
    env = {**os.environ, **load_env(LIVE / ".env")}
    env.update({
        "SPORTS_EDGE_DATA_DIR": str(tmp / "data"), "SPORTS_EDGE_LOG_DIR": str(tmp / "logs"),
        "SPORTS_EDGE_STATE_DIR": str(tmp / "state"), "SPORTS_EDGE_NOTIFY": "log",
        "SPORTS_EDGE_SHEET_DIR": str(tmp / "sheet"),
        "PYTHONPATH": str(code), "PYTHONDONTWRITEBYTECODE": "1", **code_env(code)})
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default=str(LIVE), help="checkout whose code runs")
    ap.add_argument("--keep", action="store_true", help="keep the temporary directory")
    ap.add_argument("--with-live-data", action="store_true",
                    help="start from a copy of the live data directory")
    ap.add_argument("--tmp", help="reuse this temporary directory (for multi-step gates)")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not cmd:
        ap.error("no command given")

    code = Path(args.code).resolve()
    tmp = Path(args.tmp) if args.tmp else Path(tempfile.mkdtemp(prefix="sports-edge-reality-"))
    for sub in ("data", "logs", "state", "sheet"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)
    if args.with_live_data and (LIVE / "data").exists() and not args.tmp:
        shutil.copytree(LIVE / "data", tmp / "data", dirs_exist_ok=True)

    env = run_env(code, tmp)

    roots = sorted({LIVE, code})
    before = snapshot_hashes(roots)
    proc = subprocess.run(cmd, cwd=str(tmp), env=env, capture_output=True, text=True)
    after = snapshot_hashes(roots)

    print(f"REALITY RUN code={code} tmp={tmp}")
    print(f"command: {' '.join(cmd)}")
    print(f"code path: {code} (wrappers run {env.get('SPORTS_EDGE_PROJ', LIVE)})")
    print(f"exit: {proc.returncode}")
    print("----- stdout -----")
    print(proc.stdout.rstrip())
    print("----- stderr -----")
    print(proc.stderr.rstrip())
    print("----- live data check -----")
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    print(f"live files hashed: {len(before)} before, {len(after)} after; changed: {len(changed)}")
    for k in changed:
        print(f"  CHANGED {k}")
    found = [m for m in MARKERS if m in proc.stdout or m in proc.stderr]
    print(f"failure markers found: {found or 'none'}")
    if not args.keep and not args.tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    if changed:
        return 2
    if proc.returncode != 0:
        return 1
    if found:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
