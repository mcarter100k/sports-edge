"""Rule: a notification is tested for whether it should reach the owner,
including every case where it must NOT."""
from datetime import timedelta

from sports_edge import health
from sports_edge.timeutil import parse_utc

T = parse_utc("2026-10-22T12:00:00Z")


def test_first_ever_failure_alerts():
    assert health.record_failure("snapshot", "boom", T) == "ALERT:snapshot failed: boom"


def test_repeat_within_an_hour_is_silent_then_reminds():
    health.record_failure("snapshot", "boom", T)
    assert health.record_failure("snapshot", "boom", T + timedelta(minutes=15)) is None
    assert health.record_failure("snapshot", "boom", T + timedelta(minutes=45)) is None
    msg = health.record_failure("snapshot", "boom", T + timedelta(minutes=60))
    assert msg == "ALERT:snapshot still failing (4 in a row): boom"
    assert health.record_failure("snapshot", "boom", T + timedelta(minutes=75)) is None


def test_recovery_notifies_once():
    health.record_failure("snapshot", "boom", T)
    msg = health.record_success("snapshot", T + timedelta(minutes=15))
    assert msg.startswith("RECOVERED:snapshot ok again") and "after 1 failure(s)" in msg
    assert health.record_success("snapshot", T + timedelta(minutes=30)) is None


def test_success_after_success_is_silent():
    assert health.record_success("grader", T) is None
    assert health.record_success("grader", T + timedelta(days=1)) is None


def test_failure_after_recovery_alerts_immediately():
    health.record_failure("snapshot", "a", T)
    health.record_success("snapshot", T + timedelta(minutes=15))
    assert health.record_failure("snapshot", "b", T + timedelta(minutes=30)) == "ALERT:snapshot failed: b"


def test_jobs_are_independent():
    health.record_failure("snapshot", "a", T)
    assert health.record_failure("grader", "b", T) == "ALERT:grader failed: b"


def test_cli(capsys):
    assert health.main(["record-failure", "snapshot", "exit", "1:", "x"]) == 0
    assert capsys.readouterr().out.strip() == "ALERT:snapshot failed: exit 1: x"
    assert health.main(["bogus"]) == 2
