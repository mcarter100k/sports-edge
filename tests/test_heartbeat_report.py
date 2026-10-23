from datetime import timedelta

from helpers import fee_table
from sports_edge import health
from sports_edge.heartbeat import build_report
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

NOW = parse_utc("2026-10-25T16:00:00Z")
BIG = 50 * 1024 ** 3


def seed_healthy(s, sports=("Basketball", "Soccer", "Hockey"), runs=None):
    first = NOW - timedelta(days=7)
    n = runs if runs is not None else 7 * 24 * 4
    s.append("job_runs", [{"run_id": f"r{i}", "job": "snapshot", "phase": "finish",
                           "ts_utc": first + timedelta(minutes=15 * i, seconds=30), "status": "ok",
                           "summary": "{}"} for i in range(n)])
    s.append("job_runs", [{"run_id": "r-start", "job": "snapshot", "phase": "start", "ts_utc": first,
                           "status": "running", "summary": "{}"}])
    s.upsert("markets", [{"ticker": f"T{i}", "sport": sp, "first_seen_utc": NOW, "last_seen_utc": NOW}
                         for i, sp in enumerate(sports)], "ticker")
    s.append("price_snapshots", [{"run_id": "x", "ts_utc": NOW - timedelta(hours=1), "tier": "near",
                                  "ticker": f"T{i}", "status": "active"} for i in range(len(sports))])
    health.record_success("snapshot", NOW - timedelta(minutes=10))
    health.record_success("grader", NOW - timedelta(hours=12))
    health.record_success("backup", NOW - timedelta(hours=12))


def test_healthy(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    fee_table(tmp_path)
    with Store.open() as s:
        seed_healthy(s)
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert lines[0] == "healthy", lines
    assert attention is False


def test_each_problem_is_flagged(monkeypatch, tmp_path):
    fee_table(tmp_path)
    checks = [
        (dict(), "odds NOT CONFIGURED"),
        (dict(tz="UTC"), "system time zone is UTC"),
        (dict(free=2 * 1024 ** 3), "under 5 GB"),
        (dict(sports=("Basketball",)), "only 1 sport(s) with matched snapshots"),
        (dict(runs=500), "SHORTFALL"),
    ]
    for kw, expect in checks:
        monkeypatch.setenv("ODDS_API_KEY", "" if expect.startswith("odds") else "k")
        with Store.open() as s:
            s.con.execute("DELETE FROM job_runs"); s.con.execute("DELETE FROM price_snapshots")
            s.con.execute("DELETE FROM markets")
            seed_healthy(s, kw.get("sports", ("Basketball", "Soccer", "Hockey")), kw.get("runs"))
        lines, attention = build_report(NOW, kw.get("tz", "America/Los_Angeles"), kw.get("free", BIG))
        assert attention and lines[0].startswith("NEEDS ATTENTION"), (kw, lines)
        assert any(expect in ln for ln in lines), (expect, lines)


def test_failing_job_is_flagged(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    with Store.open() as s:
        seed_healthy(s)
    health.record_failure("grader", "exit 1: boom", NOW)
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and "grader failing (1 in a row)" in lines[0]


def test_never_run_is_not_healthy():
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and "snapshot has never succeeded" in lines[0]


def test_missing_fee_table_is_flagged(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    with Store.open() as s:
        seed_healthy(s)
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and any("fee table MISSING" in ln for ln in lines)


def test_daily_only_sports_do_not_count_as_matched(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    fee_table(tmp_path)
    with Store.open() as s:
        seed_healthy(s)
        s.con.execute("UPDATE price_snapshots SET tier = 'daily'")
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and "only 0 sport(s) with matched snapshots" in lines[0]


def test_exchange_closed_runs_count_as_completed_but_too_many_are_flagged(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    fee_table(tmp_path)
    with Store.open() as s:
        seed_healthy(s)
        s.con.execute("UPDATE job_runs SET status = 'exchange_closed' WHERE run_id IN ('r1','r2','r3','r4','r5','r6','r7','r8')")
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert not attention, lines                       # the weekly window: fine
    with Store.open() as s:
        s.con.execute("UPDATE job_runs SET status = 'exchange_closed' WHERE run_id IN ('r10','r11','r12','r13','r14')")
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and any("exchange reported closed on 13 runs" in ln for ln in lines)


def test_backup_never_run_or_stale_is_flagged(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    fee_table(tmp_path)
    with Store.open() as s:
        seed_healthy(s)
    health.record_success("backup", NOW - timedelta(hours=49))
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and any("backup last succeeded" in ln and "over 48 h ago" in ln for ln in lines), lines
    health.record_success("backup", NOW - timedelta(hours=47))
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert lines[0] == "healthy", lines


def test_backup_without_any_success_is_flagged(monkeypatch, tmp_path):
    import json
    from sports_edge import config
    monkeypatch.setenv("ODDS_API_KEY", "k")
    fee_table(tmp_path)
    with Store.open() as s:
        seed_healthy(s)
    path = config.state_dir() / "health.json"
    state = json.loads(path.read_text())
    state.pop("backup")
    path.write_text(json.dumps(state))
    lines, attention = build_report(NOW, "America/Los_Angeles", BIG)
    assert attention and any("backup has never succeeded" in ln for ln in lines), lines
