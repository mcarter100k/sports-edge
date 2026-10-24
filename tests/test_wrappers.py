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
if [ "$2" = "sports_edge.jobguard" ]; then
  echo "${*:3}" >> "$SPORTS_EDGE_LOG_DIR/guard_calls"; shift 2; set -- -m "$@"
fi
if [ "$2" = "sports_edge.health" ]; then exec "$REAL_PY" "$@"; fi
if [ "$2" = "sports_edge.heartbeat" ]; then
  case "$FAKE_MODE" in
    hb_ok) printf 'healthy\\nas of now\\n'; exit 0;;
    hb_attention) printf 'NEEDS ATTENTION | odds NOT CONFIGURED\\nmore\\n'; exit 1;;
    hb_crash) printf 'Traceback (most recent call last):\\n  File "x"\\nKeyError: y\\n' >&2; exit 1;;
  esac
fi
if [ "$2" = "sports_edge.slate" ]; then
  echo "slate $3" >> "$SPORTS_EDGE_LOG_DIR/stub_calls"
  case "${FAKE_SLATE:-ok}" in
    ok) if [ "$3" = "morning" ]; then printf 'Kalshi slate · Fri Oct 23\nPractice · 0 of 100 games\nSLATE MORNING OK report=x sheet=y\n'
        else echo "SLATE ALERT OK messages=1"; fi; exit 0;;
    send_fail) echo "SLATE FAILED: SendFailed: osascript exit 1: Not authorized" >&2; exit 1;;
    tb0) echo "SLATE MORNING OK report=x"; printf 'Traceback (most recent call last):\n  File "x"\nValueError: hidden\n' >&2; exit 0;;
    no_ok) echo "something else"; exit 0;;
  esac
fi
case "$FAKE_MODE" in
  ok) echo "SNAPSHOT OK fake"; exit 0;;
  ok_with_unmatched) echo "SNAPSHOT OK fake"; echo "UNMATCHED E1 sport=Soccer reason=no_candidate" >&2; exit 0;;
  fail) echo "partial"; echo "KalshiError: GET /events failed: boom" >&2; exit 1;;
  traceback) echo "out"; printf 'Traceback (most recent call last):\\n  File "x"\\nValueError: hidden\\n' >&2; exit 0;;
  empty) exit 0;;
  timeout) printf 'Timeout (0:36:40)!\nThread 0x1 (most recent call first):\n  File "x", line 1 in read\n' >&2; exit 1;;
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


# The slate alert step inside the 15-minute job (docs/specs/slate_alerts_spec.md) --------

def stub_calls():
    p = Path(os.environ["SPORTS_EDGE_LOG_DIR"]) / "stub_calls"
    return p.read_text().splitlines() if p.exists() else []


def test_alert_step_runs_after_every_snapshot(proj, tmp_path):
    assert run("run_snapshot.sh", proj, tmp_path, "ok").returncode == 0
    assert stub_calls() == ["slate alert"] and alerts() == []
    assert "SLATE ALERT OK" in day_log("slate_alert")


def test_alert_step_runs_even_when_the_snapshot_failed(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "fail")
    assert r.returncode == 1 and stub_calls() == ["slate alert"]       # it is what sends "Don't bet now"


def test_a_failed_alert_step_is_loud_and_nonzero(proj, tmp_path):
    r = run("run_snapshot.sh", proj, tmp_path, "ok", extra={"FAKE_SLATE": "send_fail"})
    assert r.returncode == 1
    (line,) = alerts()
    assert "slate_alert failed: exit 1: SLATE FAILED: SendFailed: osascript exit 1" in line


# Morning wrapper (replaces the digest job) ------------------------------------------------

def test_morning_success_is_quiet_here_because_the_imessage_is_the_delivery(proj, tmp_path):
    r = run("run_morning.sh", proj, tmp_path, "ok")
    assert r.returncode == 0, r.stderr
    assert stub_calls() == ["slate morning"] and alerts() == []
    assert "Kalshi slate · Fri Oct 23" in day_log("morning")


@pytest.mark.parametrize("mode,reason", [
    ("send_fail", "MORNING FAILED | exit 1: SLATE FAILED: SendFailed: osascript exit 1"),
    ("tb0", "MORNING FAILED | traceback with exit 0: ValueError: hidden"),
    ("no_ok", "MORNING FAILED | no SLATE MORNING OK line in the output"),
])
def test_morning_failures_are_loud_and_nonzero(proj, tmp_path, mode, reason):
    calls = tmp_path / "tn_calls"
    make_exe(tmp_path / "bin" / "terminal-notifier", f'#!/bin/bash\necho "$@" >> {calls}\nexit 0\n')
    r = run("run_morning.sh", proj, tmp_path, "ok", notify="on", extra={"FAKE_SLATE": mode})
    assert r.returncode == 1
    text = calls.read_text()
    assert reason in text and "-sound Basso" in text and "-subtitle ALERT" in text
    assert reason in day_log("morning")


def test_morning_missing_env_file_is_a_failure(proj, tmp_path):
    r = run("run_morning.sh", proj, tmp_path, "ok", env_file=True)
    assert r.returncode == 1 and stub_calls() == []
    assert "MORNING FAILED | exit 1: .env missing at" in alerts()[0]


# The job time limit and the stale-snapshot watch (amendment 2026-10-24) ---------------------

def guard_calls():
    p = Path(os.environ["SPORTS_EDGE_LOG_DIR"]) / "guard_calls"
    return p.read_text().splitlines() if p.exists() else []


def test_every_wrapper_starts_its_module_through_the_time_limit(proj, tmp_path):
    for script, mode in [("run_snapshot.sh", "ok"), ("run_grader.sh", "ok"), ("run_backup.sh", "ok"),
                         ("run_morning.sh", "ok"), ("weekly_heartbeat.sh", "hb_ok"), ("run_watch.sh", "ok")]:
        run(script, proj, tmp_path, mode)
    assert guard_calls() == ["sports_edge.snapshot", "sports_edge.slate alert", "sports_edge.grader",
                             "sports_edge.backup run", "sports_edge.slate morning", "sports_edge.heartbeat",
                             "sports_edge.health check-stale snapshot"]


def test_a_run_stopped_at_the_limit_alerts_with_that_reason(proj, tmp_path):
    assert run("run_snapshot.sh", proj, tmp_path, "timeout").returncode == 1
    assert "snapshot failed: exit 1: stopped at the job time limit: Timeout (0:36:40)!" in alerts()[0]


def test_the_watch_alerts_once_when_no_snapshot_has_finished_then_recovers(proj, tmp_path):
    assert run("run_watch.sh", proj, tmp_path, "ok").returncode == 1        # nothing recorded yet
    assert run("run_watch.sh", proj, tmp_path, "ok").returncode == 1        # still stale: not re-alerted
    (line,) = alerts()
    assert "snapshot_watch failed: exit 1: SNAPSHOT STALE: no finished run recorded" in line
    assert run("run_snapshot.sh", proj, tmp_path, "fail").returncode == 1   # a failed run still finished
    assert run("run_watch.sh", proj, tmp_path, "ok").returncode == 0
    assert "RECOVERED logged-only: snapshot_watch ok again" in alerts()[-1]
