"""The scheduled wrappers, run under /bin/bash 3.2 exactly as launchd runs them."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

STUB = """#!/bin/bash
# Fake interpreter: health runs for real; jobs behave per FAKE_MODE.
if [ "$2" = "sports_edge.health" ]; then exec "$REAL_PY" "$@"; fi
if [ "$2" = "sports_edge.heartbeat" ]; then
  case "$FAKE_MODE" in
    hb_ok) printf 'healthy\\nas of now\\n'; exit 0;;
    hb_attention) printf 'NEEDS ATTENTION | odds NOT CONFIGURED\\nmore\\n'; exit 1;;
    hb_crash) printf 'Traceback (most recent call last):\\n  File "x"\\nKeyError: y\\n' >&2; exit 1;;
  esac
fi
case "$FAKE_MODE" in
  ok) echo "SNAPSHOT OK fake"; exit 0;;
  ok_with_unmatched) echo "SNAPSHOT OK fake"; echo "UNMATCHED E1 sport=Soccer reason=no_candidate" >&2; exit 0;;
  fail) echo "partial"; echo "KalshiError: GET /events failed: boom" >&2; exit 1;;
  traceback) echo "out"; printf 'Traceback (most recent call last):\\n  File "x"\\nValueError: hidden\\n' >&2; exit 0;;
  empty) exit 0;;
  forced) case "$KALSHI_BASE_URL" in *forced-failure*) echo "KalshiError: forced DNS failure" >&2; exit 1;;
          *) echo "SNAPSHOT OK"; exit 0;; esac;;
esac
echo "unknown FAKE_MODE $FAKE_MODE" >&2; exit 9
"""


def make_exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    shutil.copytree(REPO / "scripts", p / "scripts")
    (tmp_path / "bin").mkdir()
    make_exe(tmp_path / "bin" / "fakepy", STUB)
    return p


def run(script, proj, tmp_path, mode, env_file=False, notify="log", extra=None):
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path),
        "SPORTS_EDGE_PROJ": str(proj), "SPORTS_EDGE_PYTHON": str(tmp_path / "bin" / "fakepy"),
        "REAL_PY": sys.executable, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1",
        "SPORTS_EDGE_LOG_DIR": os.environ["SPORTS_EDGE_LOG_DIR"],
        "SPORTS_EDGE_STATE_DIR": os.environ["SPORTS_EDGE_STATE_DIR"],
        "SPORTS_EDGE_DATA_DIR": os.environ["SPORTS_EDGE_DATA_DIR"],
        "SPORTS_EDGE_NOTIFY": notify, "FAKE_MODE": mode, "NOTIFY_WATCHDOG_SECS": "3",
        "SPORTS_EDGE_TN_BIN": str(tmp_path / "bin" / "terminal-notifier"),
        **(extra or {})}
    if not env_file:
        env["SPORTS_EDGE_NO_ENV_FILE"] = "1"
    return subprocess.run(["/bin/bash", str(proj / "scripts" / script)], env=env,
                          capture_output=True, text=True, timeout=60)


def alerts():
    p = Path(os.environ["SPORTS_EDGE_LOG_DIR"]) / "alerts.log"
    return p.read_text().splitlines() if p.exists() else []


def day_log(job):
    logs = list(Path(os.environ["SPORTS_EDGE_LOG_DIR"]).glob(f"{job}_*.log"))
    return logs[0].read_text() if logs else ""


def test_launchd_runs_bash_32():
    out = subprocess.run(["/bin/bash", "--version"], capture_output=True, text=True).stdout
    assert "version 3.2" in out


def test_success_exits_zero_and_does_not_notify(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "ok")
    assert r.returncode == 0 and alerts() == []
    assert "SNAPSHOT OK fake" in day_log("snapshot")


def test_unmatched_lines_on_stderr_are_not_a_failure(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "ok_with_unmatched")
    assert r.returncode == 0 and alerts() == []
    assert "UNMATCHED E1" in day_log("snapshot")


def test_failure_exits_nonzero_and_alerts_with_reason(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "fail")
    assert r.returncode == 1
    (line,) = alerts()
    assert "ALERT logged-only: snapshot failed: exit 1: KalshiError: GET /events failed: boom" in line


def test_traceback_with_exit_zero_is_a_failure(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "traceback")
    assert r.returncode == 1
    assert "ValueError: hidden" in alerts()[0]


def test_empty_output_is_a_failure(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "empty")
    assert r.returncode == 1 and "no output" in alerts()[0]


def test_missing_env_file_is_a_failure(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "ok", env_file=True)
    assert r.returncode == 1 and ".env missing" in alerts()[0]


def test_repeat_failure_is_rate_limited_then_recovery_notifies(proj, tmp_path):
    assert run("run_snapshot.sh", proj, tmp_path, "fail").returncode == 1
    assert run("run_snapshot.sh", proj, tmp_path, "fail").returncode == 1   # still nonzero
    assert len(alerts()) == 1                                                # but not re-alerted
    assert run("run_snapshot.sh", proj, tmp_path, "ok").returncode == 0
    assert "RECOVERED logged-only: snapshot ok again" in alerts()[1]


def test_forced_failure_switch_fails_once_then_recovers(proj, tmp_path):
    flag = Path(os.environ["SPORTS_EDGE_STATE_DIR"]) / "force_failure_once"
    flag.write_text("")
    assert run("run_snapshot.sh", proj, tmp_path, "forced").returncode == 1
    assert not flag.exists()
    assert "forced DNS failure" in alerts()[0]
    assert "FORCED FAILURE requested" in day_log("snapshot")
    assert run("run_snapshot.sh", proj, tmp_path, "forced").returncode == 0
    assert "RECOVERED" in alerts()[1]


def test_grader_wrapper_fails_loudly(proj, tmp_path):
    assert run("run_grader.sh", proj, tmp_path, "fail").returncode == 1
    assert "grader failed" in alerts()[0]


# Delivery ------------------------------------------------------------------

def test_terminal_notifier_used_with_sound_for_alerts(proj, tmp_path):
    calls = tmp_path / "tn_calls"
    make_exe(tmp_path / "bin" / "terminal-notifier", f'#!/bin/bash\necho "$@" >> {calls}\nexit 0\n')
    run("run_snapshot.sh", proj, tmp_path, "fail", notify="on")
    assert "-sound Basso" in calls.read_text() and "-subtitle ALERT" in calls.read_text()
    assert "terminal-notifier:" in alerts()[0]


def test_falls_back_to_osascript(proj, tmp_path):
    calls = tmp_path / "osa_calls"
    make_exe(tmp_path / "bin" / "terminal-notifier", "#!/bin/bash\nexit 1\n")
    make_exe(tmp_path / "bin" / "osascript", f'#!/bin/bash\necho "$@" >> {calls}\nexit 0\n')
    run("run_snapshot.sh", proj, tmp_path, "fail", notify="on")
    assert 'sound name "Basso"' in calls.read_text()
    assert "osascript:" in alerts()[0]


def test_both_delivery_methods_failing_is_recorded(proj, tmp_path):
    make_exe(tmp_path / "bin" / "terminal-notifier", "#!/bin/bash\nexit 1\n")
    make_exe(tmp_path / "bin" / "osascript", "#!/bin/bash\nexit 1\n")
    r = run("run_snapshot.sh", proj, tmp_path, "fail", notify="on")
    assert r.returncode == 1
    assert "ALERT DELIVERY FAILED" in alerts()[0]
    assert "ALERT DELIVERY FAILED" in day_log("snapshot")


def test_hung_notifier_is_killed_and_falls_back(proj, tmp_path):
    make_exe(tmp_path / "bin" / "terminal-notifier", "#!/bin/bash\nsleep 30\n")
    make_exe(tmp_path / "bin" / "osascript", "#!/bin/bash\nexit 0\n")
    r = run("run_snapshot.sh", proj, tmp_path, "fail", notify="on")
    assert r.returncode == 1 and "osascript:" in alerts()[0]


# Weekly heartbeat ---------------------------------------------------------------

def test_heartbeat_always_notifies_when_healthy(proj, tmp_path):
    r = run("weekly_heartbeat.sh", proj, tmp_path, "hb_ok")
    assert r.returncode == 0
    assert "HEARTBEAT logged-only: healthy" in alerts()[0]


def test_heartbeat_attention_is_loud_and_nonzero(proj, tmp_path):
    calls = tmp_path / "tn_calls"
    make_exe(tmp_path / "bin" / "terminal-notifier", f'#!/bin/bash\necho "$@" >> {calls}\nexit 0\n')
    r = run("weekly_heartbeat.sh", proj, tmp_path, "hb_attention", notify="on")
    assert r.returncode == 1
    assert "NEEDS ATTENTION | odds NOT CONFIGURED" in calls.read_text()
    assert "-sound Basso" in calls.read_text()


def test_heartbeat_crash_still_notifies(proj, tmp_path):
    r = run("weekly_heartbeat.sh", proj, tmp_path, "hb_crash")
    assert r.returncode == 1
    assert "heartbeat report failed: KeyError: y" in alerts()[0]
