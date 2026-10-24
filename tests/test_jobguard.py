"""sports_edge/jobguard.py: a hung job exits within the time limit (docs/specs/heartbeat_spec.md)."""
import os
import re
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from sports_edge import health
from sports_edge.timeutil import utcnow

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


def test_the_limits_are_the_reviewed_table():
    from sports_edge import jobguard
    assert jobguard.LIMITS_S == {"sports_edge.snapshot": 720, "sports_edge.grader": 720, "sports_edge.slate": 240,
                                 "sports_edge.heartbeat": 240, "sports_edge.health": 120, "sports_edge.backup": 2200}
    assert jobguard.SNAPSHOT_TIER_S == {"near": 200, "hourly": 120, "daily": 2200}
    assert "SPORTS_EDGE_JOB_LIMIT_S" not in os.environ


def test_every_wrapper_module_has_a_limit():
    from sports_edge import jobguard
    called = set()
    for script in (REPO / "scripts").glob("*.sh"):
        text = script.read_text()
        called |= set(re.findall(r"run_job \S+ (sports_edge\.\w+)", text))
        called |= set(re.findall(r"-m sports_edge\.jobguard (sports_edge\.\w+)", text))
    assert called and called <= set(jobguard.LIMITS_S), called - set(jobguard.LIMITS_S)


def test_an_unknown_module_is_refused_not_run_without_a_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("SPORTS_EDGE_JOB_LIMIT_S", raising=False)
    (tmp_path / "nolimit.py").write_text("print('ran')\n")
    env = {**os.environ, "PYTHONPATH": f"{REPO}:{tmp_path}"}
    env.pop("SPORTS_EDGE_JOB_LIMIT_S", None)
    r = subprocess.run([sys.executable, "-m", "sports_edge.jobguard", "nolimit"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "no time limit set for nolimit" in r.stderr and "ran" not in r.stdout


SETS_ITS_OWN = """import socket
from sports_edge import jobguard
jobguard.set_limit(1)        # as the snapshot does once it knows its tier
a, b = socket.socketpair()
a.recv(1)
"""


def test_set_limit_restarts_the_limit_inside_jobguard(tmp_path):
    """Module limit 60 s; the job narrows it to 1 s and then hangs: it must stop near 1 s."""
    (tmp_path / "narrows.py").write_text(SETS_ITS_OWN)
    driver = ("import sys; from sports_edge import jobguard; jobguard.LIMITS_S['narrows'] = 60; "
              "sys.argv = ['x', 'narrows']; import runpy; runpy.run_module('sports_edge.jobguard', run_name='__main__')")
    env = {k: v for k, v in os.environ.items() if k != "SPORTS_EDGE_JOB_LIMIT_S"}
    env["PYTHONPATH"] = f"{REPO}:{tmp_path}"
    start = time.monotonic()
    r = subprocess.run([sys.executable, "-c", driver], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and time.monotonic() - start < 10       # run as -m runs it (__main__)
    assert "\nTimeout (0:00:01)!\n" in r.stderr                    # after runpy's import warning


def test_set_limit_outside_jobguard_does_nothing(tmp_path):
    code = "import time; from sports_edge import jobguard; jobguard.set_limit(0.2); time.sleep(1); print('alive')"
    r = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(REPO)},
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout == "alive\n"


# health.check_stale --------------------------------------------------------------------

def test_nothing_recorded_is_stale():
    assert health.check_stale("snapshot") == (False, "SNAPSHOT STALE: no finished run recorded")


def test_a_recent_success_is_fresh_and_an_old_one_is_stale():
    now = utcnow()
    health.record_success("snapshot", now=now - timedelta(minutes=59))
    assert health.check_stale("snapshot", now=now)[0] is True
    fresh, line = health.check_stale("snapshot", now=now + timedelta(minutes=2))
    assert not fresh and line.startswith("SNAPSHOT STALE: no run finished since") and line.endswith("(61 min)")


def test_a_recent_failure_counts_as_a_finished_run():
    now = utcnow()
    health.record_success("snapshot", now=now - timedelta(hours=6))
    health.record_failure("snapshot", "boom", now=now - timedelta(minutes=5))
    assert health.check_stale("snapshot", now=now)[0] is True
