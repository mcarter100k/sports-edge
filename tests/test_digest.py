"""Daily digest wording and counting (docs/specs/daily_digest_spec.md, Part A)."""
import json
from datetime import timedelta

import pytest

from helpers import fee_table
from sports_edge import config, digest, grader, health, ledger, shadow
from sports_edge.store import Store
from sports_edge.timeutil import iso_z, parse_utc

NOW = parse_utc("2026-10-22T14:30:00Z")            # 07:30 PDT
Y0 = parse_utc("2026-10-21T07:00:00Z")             # yesterday 00:00 PDT
T0 = parse_utc("2026-10-22T07:00:00Z")             # today 00:00 PDT


def run(s, ts, summary=None, status="ok"):
    s.append("job_runs", [{"run_id": iso_z(ts), "job": "snapshot", "phase": "finish", "ts_utc": ts,
                           "status": status, "summary": json.dumps(summary or {})}])


def snap(s, ticker, event, sport, ts, tier="near"):
    s.upsert("markets", [{"ticker": ticker, "event_ticker": event, "sport": sport,
                          "first_seen_utc": ts, "last_seen_utc": ts}], "ticker")
    s.append("price_snapshots", [{"run_id": "x", "ts_utc": ts, "tier": tier, "ticker": ticker,
                                  "status": "active"}])


def shadow_row(row_id, market, event, side, logged, rule="GAP_v2"):
    return {"row_id": row_id, "schema_version": "2", "rule_id": rule, "logged_utc": iso_z(logged),
            "snapshot_utc": iso_z(logged), "event_ticker": event, "market_ticker": market, "side": side}


def grade_row(row_id, market, event, side, snapshot, pnl, price="0.40", fee="0.02", clv="",
              graded=NOW - timedelta(hours=4), rule="GAP_v2"):
    return {"row_id": row_id, "market_ticker": market, "event_ticker": event, "side": side,
            "rule_id": rule, "snapshot_utc": iso_z(snapshot), "result": "yes", "payout": "1",
            "price_paid": price, "fee_per_contract": fee, "pnl_per_contract": pnl, "clv": clv,
            "graded_utc": iso_z(graded)}


@pytest.fixture
def healthy(monkeypatch, tmp_path):
    monkeypatch.setenv("ODDS_API_KEY", "k")
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "15")
    fee_table(tmp_path)
    health.record_success("snapshot", NOW - timedelta(minutes=10))
    health.record_success("grader", NOW - timedelta(hours=4))


def seed_day(s):
    run(s, Y0 + timedelta(hours=1), {"shadow_checked": 6, "skip_reference_stale": 2, "skip_insufficient_depth": 1})
    run(s, Y0 + timedelta(hours=2), {"shadow_checked": 4, "skip_reference_stale": 1, "skip_fee_unknown": 9})
    snap(s, "NBA1-A", "NBA1", "Basketball", Y0 + timedelta(hours=1))
    snap(s, "NBA1-B", "NBA1", "Basketball", Y0 + timedelta(hours=1))
    snap(s, "NBA2-A", "NBA2", "Basketball", Y0 + timedelta(hours=3))
    snap(s, "EPL1-A", "EPL1", "Soccer", Y0 + timedelta(hours=5), tier="hourly")
    snap(s, "CHESS1", "CH1", "Chess", Y0 + timedelta(hours=1), tier="daily")    # full scan only
    snap(s, "NBA2-A", "NBA2", "Basketball", Y0 + timedelta(hours=1), tier="daily")
    s.append("odds_calls", [{"ts_utc": Y0 + timedelta(hours=1), "endpoint": "odds", "credits_last": 3},
                            {"ts_utc": Y0 + timedelta(hours=9), "endpoint": "odds", "credits_last": 2}])


def test_full_digest_wording(healthy):
    with Store.open() as s:
        seed_day(s)
    ledger.append_rows(config.shadow_ledger_path(), shadow.HEADER, [
        shadow_row("r1", "NBA1-A", "NBA1", "yes", Y0 + timedelta(hours=1)),
        shadow_row("r2", "NBA1-B", "NBA1", "no", Y0 + timedelta(hours=1)),   # same game, other side
        shadow_row("r3", "EPL1-A", "EPL1", "yes", Y0 + timedelta(hours=5)),
    ])
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, [
        grade_row("r1", "NBA1-A", "NBA1", "yes", Y0, "0.58", clv="0.03"),
        grade_row("r2", "NBA1-B", "NBA1", "no", Y0, "-0.42", clv="-0.01"),
        grade_row("r3", "EPL1-A", "EPL1", "yes", Y0, "0.58", clv="0.02"),
    ])
    line, report, _ = digest.build_digest(NOW)
    assert line == (
        "Games watched yesterday: 3 (Basketball 2, Soccer 1) · Underpriced bets spotted: 2 · "
        "Finished games scored: 2 · If you'd bet all of them: up 58.7¢ per $1 "
        "(2 games — too early to trust under 20) · Beat the final pre-game market price: 2 of 3 times · "
        "Skipped: 4 (prices too old, or not enough contracts for sale) · System: OK · "
        "Odds credits used: 5 of 15")
    assert report[0] == line
    assert any("PDT" in r for r in report)
    assert not any("edge" in r.lower() and "not" not in r.lower() and "nothing" not in r.lower()
                   for r in report)


def test_only_first_fire_counts_toward_money_and_closing_price(healthy):
    with Store.open() as s:
        seed_day(s)
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, [
        grade_row("r1", "NBA1-A", "NBA1", "yes", Y0, "-0.42", clv="-0.02"),
        grade_row("r9", "NBA1-A", "NBA1", "yes", Y0 + timedelta(hours=2), "0.90", clv="0.30"),
    ])
    line, _, _ = digest.build_digest(NOW)
    assert "down 100¢ per $1 (1 game — too early" in line
    assert "Beat the final pre-game market price: 0 of 1 times" in line


def test_first_fire_parity_with_the_grader(healthy):
    rows = [grade_row(f"r{i}", f"M{i % 4}", f"E{i % 3}", "yes" if i % 2 else "no",
                      Y0 + timedelta(minutes=i), "0.10") for i in range(12)]
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, rows)
    first = digest.first_fire(ledger.read_rows(config.shadow_grades_path()), "snapshot_utc")
    report = " ".join(grader.shadow_report())
    assert f"first-fire {len(first)};" in report
    assert f"independent events {len({r['event_ticker'] for r in first})}" in report


def test_caveat_drops_at_twenty_games(healthy):
    rows = [grade_row(f"r{i}", f"M{i}", f"E{i}", "yes", Y0, "0.10") for i in range(20)]
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, rows)
    line, _, _ = digest.build_digest(NOW)
    assert "(20 games) ·" in line and "too early" not in line
    rows19 = digest.first_fire(rows[:19], "snapshot_utc")
    assert "too early to trust under 20" in digest.money_line(rows19)[0]


def test_even_and_no_bets_wording():
    assert digest.money_line([])[0] == "If you'd bet all of them: no finished bets yet"
    even = [grade_row("a", "M", "E", "yes", Y0, "0")]
    assert digest.money_line(even)[0].startswith("If you'd bet all of them: even per $1 (1 game")
    assert digest.clv_line(even) == "Beat the final pre-game market price: no closing prices yet"
    tie = [grade_row("a", "M", "E", "yes", Y0, "0", clv="0")]
    assert digest.clv_line(tie) == "Beat the final pre-game market price: 0 of 1 times"


def test_a_zero_from_a_path_that_cannot_produce_one_is_named(monkeypatch, tmp_path):
    monkeypatch.delenv("SPORTS_EDGE_FEE_TABLE", raising=False)
    monkeypatch.setenv("SPORTS_EDGE_FEE_TABLE", str(tmp_path / "absent.json"))
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    line, _, _ = digest.build_digest(NOW)
    assert "Games watched yesterday: no snapshot run finished yesterday" in line
    assert "Underpriced bets spotted: none possible — no fee table" in line
    assert "Finished games scored: grader has never run" in line
    assert "Skipped: no snapshot run finished yesterday" in line
    assert "Odds credits used: not configured" in line
    assert "System: problems — snapshot has never succeeded; grader has never succeeded; " \
           "no snapshot run finished yesterday; fee table missing; odds not configured" in line


def test_failing_job_is_a_problem(healthy):
    with Store.open() as s:
        seed_day(s)
    health.record_failure("grader", "boom", NOW - timedelta(hours=3))
    line, _, _ = digest.build_digest(NOW)
    assert "System: problems — grader failing (1 in a row)" in line


def test_window_edges_are_pacific_midnight(healthy):
    with Store.open() as s:
        run(s, Y0, {"shadow_checked": 1})                           # included (start)
        run(s, Y0 - timedelta(seconds=1), {"skip_reference_stale": 50})   # day before
        run(s, T0, {"skip_insufficient_depth": 70})                 # today: excluded
        snap(s, "A", "EA", "Tennis", Y0)
        snap(s, "B", "EB", "Tennis", T0)
        s.append("odds_calls", [{"ts_utc": Y0, "endpoint": "odds", "credits_last": 4},
                                {"ts_utc": T0, "endpoint": "odds", "credits_last": 6}])
    ledger.append_rows(config.shadow_ledger_path(), shadow.HEADER, [
        shadow_row("in", "A", "EA", "yes", Y0), shadow_row("in2", "A2", "EA2", "yes", Y0),
        shadow_row("out", "B", "EB", "yes", T0)])           # 2 at the start edge, 1 at the end edge
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, [
        grade_row("g1", "A", "EA", "yes", Y0, "0.1", graded=T0 - timedelta(seconds=1)),   # yesterday's run
        grade_row("g2", "B", "EB", "yes", Y0, "0.1", graded=T0 + timedelta(hours=3))])
    line, _, _ = digest.build_digest(NOW)
    assert "Games watched yesterday: 1 (Tennis 1)" in line
    assert "Underpriced bets spotted: 2" in line
    assert "Finished games scored: 1" in line
    assert "Skipped: 0 (" in line
    assert "Odds credits used: 4 of 15" in line


def test_report_file_is_never_overwritten(healthy):
    first = digest.write_report(["one"], NOW)
    second = digest.write_report(["two"], NOW)
    assert first.name == "digest_2026-10-22.txt" and second.name == "digest_2026-10-22_2.txt"
    assert first.read_text() == "one\n" and second.read_text() == "two\n"
    assert first.parent == config.logs_dir()


def test_main_prints_line_then_report_path(healthy, capsys):
    assert digest.main() == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Sports-edge: ")
    assert out[1].startswith("report: ") and out[1].endswith(".txt")
    assert out[2].startswith("Games watched yesterday: ")


def test_full_scan_is_not_watched_and_nothing_checked_is_named(healthy):
    with Store.open() as s:
        run(s, Y0 + timedelta(hours=1), {"odds": "not_configured"})   # no shadow step ran
        snap(s, "CHESS1", "CH1", "Chess", Y0 + timedelta(hours=1), tier="daily")
        snap(s, "NBA9", "NBA9", "Basketball", Y0 + timedelta(hours=1), tier="daily")
    line, report, _ = digest.build_digest(NOW)
    assert "Games watched yesterday: none matched to reference odds" in line
    assert "Skipped: nothing checked (no games with reference odds)" in line
    assert any(r.startswith("full scan yesterday: 2 open games seen across 2 categories") for r in report)


# Banner (spec amendment: banner format) ---------------------------------------

def full_fixture():
    with Store.open() as s:
        seed_day(s)
    ledger.append_rows(config.shadow_ledger_path(), shadow.HEADER, [
        shadow_row("r1", "NBA1-A", "NBA1", "yes", Y0 + timedelta(hours=1)),
        shadow_row("r2", "NBA1-B", "NBA1", "no", Y0 + timedelta(hours=1)),
        shadow_row("r3", "EPL1-A", "EPL1", "yes", Y0 + timedelta(hours=5))])
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, [
        grade_row("r1", "NBA1-A", "NBA1", "yes", Y0, "0.58"),
        grade_row("r2", "NBA1-B", "NBA1", "no", Y0, "-0.42"),
        grade_row("r3", "EPL1-A", "EPL1", "yes", Y0, "0.58")])


def test_banner_wording(healthy):
    full_fixture()
    _, _, text = digest.build_digest(NOW)
    assert text == "Sports-edge: 3 games · 2 underpriced · 2 scored · up 58.7¢ per $1 (too early) · system OK"


def test_banner_when_nothing_is_scored_yet(healthy):
    with Store.open() as s:
        seed_day(s)
    _, _, text = digest.build_digest(NOW)
    assert text == "Sports-edge: no games scored yet · system OK"


def test_banner_names_paths_that_cannot_produce_a_number(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTS_EDGE_FEE_TABLE", str(tmp_path / "absent.json"))
    ledger.append_rows(config.shadow_grades_path(), grader.SHADOW_GRADE_HEADER, [
        grade_row("r1", "M", "E", "yes", Y0, "-0.42")])
    _, _, text = digest.build_digest(NOW)
    assert text == ("Sports-edge: no snapshot run · no fee table · grader never ran · "
                    "down 100¢ per $1 (too early) · system problems")


def test_banner_counts_games_from_twenty_and_singular_forms():
    rows = [grade_row(f"r{i}", f"M{i}", f"E{i}", "yes", Y0, "0.10") for i in range(20)]
    assert digest.banner("1 game", "1 underpriced", "1 scored", rows, []).endswith(
        "up 23.8¢ per $1 (20 games) · system OK")
    assert digest.plural(1, "game") == "1 game" and digest.plural(0, "game") == "0 games"
    assert "(too early)" in digest.banner("x", "y", "z", rows[:19], [])


def test_banner_worst_case_survives_notify_whole():
    suffix = " → digest_2026-10-23_999.txt"          # what notify.sh appends
    worst_rows = [grade_row(f"r{i}", f"M{i}", f"E{i}", "yes", Y0, "-0.4237") for i in range(19)]
    for games in ("99999 games", "no snapshot run", "no matched games"):
        for under in ("99999 underpriced", "fee table invalid", "no fee table"):
            for scored in ("99999 scored", "grader never ran"):
                text = digest.banner(games, under, scored, worst_rows, ["p"])
                assert len(text) <= digest.BANNER_MAX
                assert len(text + suffix) <= 220, text      # the osascript fallback's cut
