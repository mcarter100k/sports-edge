"""sports_edge/jobguard.py: a hung job exits within the time limit (docs/specs/heartbeat_spec.md)."""
import os
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent

HUNG = '''import socket
def wait_for_a_reply_that_never_comes():
    a, b = socket.socketpair()
    a.recv(1)          # blocks forever, like the 2026-10-24 Kalshi read
wait_for_a_reply_that_never_comes()
'''
FINISHES = '''import sys
print("args", sys.argv[1:])
sys.exit(3)
'''


def guarded(tmp_path, module, source, *args, limit="1", runner=None):
    (tmp_path / f"{module}.py").write_text(source)
    env = {**os.environ, "PYTHONPATH": f"{REPO}:{tmp_path}", "SPORTS_EDGE_JOB_LIMIT_S": limit}
    start = time.monotonic()
    r = subprocess.run(runner or [sys.executable, "-m", "sports_edge.jobguard", module, *args],
                       env=env, capture_output=True, text=True, timeout=30)
    return r, time.monotonic() - start


def test_a_hung_job_exits_1_within_the_limit_with_its_stack(tmp_path):
    r, took = guarded(tmp_path, "hungjob", HUNG)
    assert r.returncode == 1 and took < 10
    assert r.stderr.startswith("Timeout (0:00:01)!")
    assert "wait_for_a_reply_that_never_comes" in r.stderr


def test_a_job_that_finishes_keeps_its_exit_code_and_arguments(tmp_path):
    r, _ = guarded(tmp_path, "quickjob", FINISHES, "alert", "--x")
    assert r.returncode == 3 and r.stdout == "args ['alert', '--x']\n" and "Timeout" not in r.stderr


def test_a_hung_job_through_the_real_wrapper_alerts_within_the_limit(tmp_path, monkeypatch):
    """common.sh run_job -> jobguard -> the hung module, with the real interpreter and real health."""
    for k, v in {"SPORTS_EDGE_PROJ": str(REPO), "SPORTS_EDGE_PYTHON": sys.executable,
                 "SPORTS_EDGE_NO_ENV_FILE": "1", "SPORTS_EDGE_NOTIFY": "log"}.items():
        monkeypatch.setenv(k, v)
    script = f'. "{REPO}/scripts/common.sh"; run_job hungjob hungjob; echo "rc=$?"'
    r, took = guarded(tmp_path, "hungjob", HUNG, runner=["/bin/bash", "-c", script])
    assert r.stdout.strip() == "rc=1" and took < 15
    log_dir = Path(os.environ["SPORTS_EDGE_LOG_DIR"])
    day_log = next(log_dir.glob("hungjob_*.log")).read_text()
    assert "wait_for_a_reply_that_never_comes" in day_log
    assert "hungjob failed: exit 1: stopped at the job time limit: Timeout (0:00:01)!" in (
        log_dir / "alerts.log").read_text()


def test_the_two_limits():
    from sports_edge import jobguard
    assert "SPORTS_EDGE_JOB_LIMIT_S" not in os.environ
    assert jobguard.limit_s("sports_edge.snapshot") == jobguard.limit_s("sports_edge.grader") == 3600
    assert {jobguard.limit_s(m) for m in ("sports_edge.slate", "sports_edge.heartbeat", "sports_edge.backup")} == {120}
