"""The board (docs/specs/slate_alerts_spec.md, "the board") and the line wording on real runs."""
from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import fee_table
from sports_edge import bets, board, config, health, ledger, slate
from sports_edge.store import Store
from sports_edge.timeutil import iso_z
from test_slate import CHECK_AT, DAILY_AT, PRICED, START, no_jargon, run_at, world

SRC = "the-odds-api"
FIRST, SECOND = START - timedelta(minutes=55), START - timedelta(minutes=12)    # the paid plan's two checks


@pytest.fixture(autouse=True)
def odds_key(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "test-key")      # so an empty board names its real cause


def board_at(now, cap=False):
    with Store.open() as s:
        ctx = slate.Context(s, now, ingest=False)
        ctx.cap_reached = cap
        return slate.board_lines(ctx, now)


def checked_run(tmp_path, pages=None, at=CHECK_AT, priced=PRICED):
    fee_table(tmp_path)
    sers, default = world()
    run_at(DAILY_AT, sers, pages or default)
    run_at(at, sers, pages or default, priced)
    health.record_success("snapshot", at)
    return sers


def paid(tmp_path, monkeypatch, second_pages=None, second_priced=PRICED):
    monkeypatch.setenv("ODDS_PLAN", "paid")
    sers = checked_run(tmp_path, at=FIRST)
    run_at(SECOND, sers, second_pages or world()[1], second_priced)
    health.record_success("snapshot", SECOND)


def near_world():
    """Cleveland Yes at 20c is a gap; Philadelphia Yes at 60c and No at 35c are under 3c; Cleveland No is below 0."""
    sers, pages = world()
    pages[0][0]["markets"][1].update({"yes_bid_dollars": "0.5900", "yes_ask_dollars": "0.6000",
                                      "no_bid_dollars": "0.3400", "no_ask_dollars": "0.3500"})
    return pages


def test_board_shows_the_checks_gap_numbered(tmp_path):
    checked_run(tmp_path)
    lines = board_at(CHECK_AT + timedelta(minutes=2))
    assert lines[0] == "BOARD (price gaps as of each game's check, 7:37 AM PT)"
    assert lines[1].startswith("  #1 Cleveland over Philadelphia (Yes on Cleveland) · NBA · 8:00 AM PT · Pay up to ")
    assert lines[1].endswith('· Reply "took 20" or "skip"') and "no earlier Kalshi price" in lines[1]   # one read
    assert no_jargon("\n".join(lines)) == []


def test_a_paid_gap_that_closed_at_the_fifteen_minute_check_leaves_the_board(tmp_path, monkeypatch):
    paid(tmp_path, monkeypatch, second_pages=world(cheap=False)[1])
    assert board_at(SECOND + timedelta(minutes=1))[1:] == ["  none (checked, no price gaps of 3¢ or more)"]


def test_a_latest_check_with_no_sharp_price_shows_nothing_from_the_earlier_check(tmp_path, monkeypatch):
    paid(tmp_path, monkeypatch, second_priced={})
    assert board_at(SECOND + timedelta(minutes=1))[1:] == ["  none (no checked game still to start)"]


def test_a_started_game_leaves_the_board_at_its_start(tmp_path):
    checked_run(tmp_path)
    assert board_at(START)[1:] == ["  none (no checked game still to start)"]


def test_a_postponed_game_leaves_the_board_and_is_listed_postponed(tmp_path):
    checked_run(tmp_path)
    with Store.open() as s:
        s.append("reference_events", [{"fetched_utc": CHECK_AT + timedelta(minutes=1), "source": SRC,
                                       "sport_key": "basketball_nba", "source_event_id": "R1",
                                       "commence_utc": START + timedelta(minutes=60), "home": "Philadelphia 76ers",
                                       "away": "Cleveland Cavaliers"}])
    now = CHECK_AT + timedelta(minutes=2)
    assert not any("Cleveland over" in ln for ln in board_at(now))
    with Store.open() as s:
        sheet = slate.sheet_lines(slate.Context(s, now, ingest=False), now)
    assert "  #1 postponed — void (now listed 2026-10-22 09:00 PDT)" in sheet


def test_two_ways_to_back_one_team_show_once_the_cheaper(tmp_path):
    sers, pages = world()
    pages[0][0]["markets"][1].update({"no_ask_dollars": "0.1900", "no_bid_dollars": "0.1800"})
    checked_run(tmp_path, pages)
    gaps = [ln for ln in board_at(CHECK_AT + timedelta(minutes=1)) if "#1" in ln]
    assert len(gaps) == 1 and "(No on Philadelphia)" in gaps[0] and "now 19¢" in gaps[0]


def row(rid, run, edge, price="0.40", fee="0.02", event="R1", market="M1", side="YES"):
    return {"row_id": rid, "run_id": run, "edge": edge, "price_paid": price, "fee_per_contract": fee,
            "source_event_id": event, "market_ticker": market, "side": side, "rule_id": "GAP_v2",
            "commence_utc": iso_z(START)}


def test_current_gaps_come_only_from_the_latest_check_biggest_first_one_per_result():
    rows = [row("old", "run1", "0.20"), row("a", "run2", "0.04", market="M1"),
            row("twin", "run2", "0.07", market="M2", price="0.31"),      # same result as b, dearer
            row("b", "run2", "0.06", market="M2", price="0.30")]
    latest = {"R1": {"run_id": "run2"}}
    one = lambda r: (r["market_ticker"], r["side"])
    got = board.current_gaps(rows, latest, one, CHECK_AT, {"R1": START})
    assert [r["row_id"] for r in got] == ["b", "a"]


def test_near_misses_are_from_zero_up_to_three_cents_and_never_picks(tmp_path):
    checked_run(tmp_path, near_world())
    with Store.open() as s:
        latest = board.latest_checks(s, SRC, CHECK_AT + timedelta(minutes=1), {"R1": START})
        near = board.near_misses(s, SRC, latest, bets.bet_key, CHECK_AT, {"R1": START})
    assert sorted((r["market_ticker"], r["side"]) for r in near) == [
        ("KXNBAGAME-E1-PHI", "NO"), ("KXNBAGAME-E1-PHI", "YES")]
    assert all(D(0) <= r["edge"] < D("0.03") for r in near)
    assert len(ledger.read_rows(config.shadow_ledger_path())) == 1       # never written


def test_near_misses_skip_the_twin_of_a_gap_and_say_not_a_pick(tmp_path):
    checked_run(tmp_path, near_world())
    lines = board_at(CHECK_AT + timedelta(minutes=1))
    at = lines.index("  Near misses (not picks):")
    assert lines[at + 1:] == ["  Philadelphia over Cleveland (Yes on Philadelphia) · NBA · 8:00 AM PT · now 60¢ · "
                              "fair 62.7¢ · gap 1¢ after fee · not a pick"]


def test_a_near_miss_the_best_level_cannot_fill_is_not_shown(tmp_path):
    pages = near_world()
    pages[0][0]["markets"][1]["yes_ask_size_fp"] = "5.00"     # Philadelphia Yes needs 16 contracts at 60c
    checked_run(tmp_path, pages)
    assert not any("Yes on Philadelphia" in ln for ln in board_at(CHECK_AT + timedelta(minutes=1)))


@pytest.mark.parametrize("minutes,numbered", [(15, True), (16, False)])
def test_a_check_over_fifteen_minutes_old_is_an_older_check(tmp_path, minutes, numbered):
    checked_run(tmp_path)
    lines = board_at(CHECK_AT + timedelta(minutes=minutes))
    assert lines[1].startswith("  #1 Cleveland") is numbered
    if not numbered:
        assert lines[1:3] == ["  none (only older checks, see below)", "  Older checks, don't bet:"]
        assert lines[3].startswith("  Cleveland over Philadelphia") and lines[3].endswith("· old price, don't bet")


def test_a_failing_system_puts_the_check_under_older_checks(tmp_path):
    checked_run(tmp_path)
    health.record_failure("snapshot", "boom", CHECK_AT + timedelta(minutes=1))
    assert board_at(CHECK_AT + timedelta(minutes=2))[-1].endswith("· system problem, don't bet")


def test_at_the_daily_cap_gaps_are_listed_without_numbers(tmp_path):
    checked_run(tmp_path)
    lines = board_at(CHECK_AT + timedelta(minutes=2), cap=True)
    assert lines[1] == "  Daily cap reached — no more bets today."
    assert lines[2].startswith("  Cleveland over Philadelphia") and lines[2].endswith("· cap reached, don't bet")


def test_more_than_the_limit_says_how_many_more(tmp_path, monkeypatch):
    checked_run(tmp_path, near_world())
    monkeypatch.setattr(slate, "BOARD_MAX", 0)
    assert board_at(CHECK_AT + timedelta(minutes=1))[1] == "  +1 more"


def test_an_empty_board_names_a_missing_fee_table():
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    assert board_at(CHECK_AT + timedelta(minutes=1))[1:] == ["  none (no fee table)"]


def test_the_board_command_writes_nothing_and_leads_the_sheet(tmp_path):
    checked_run(tmp_path, near_world())
    out = []
    slate.run_board(CHECK_AT + timedelta(minutes=1), out.append)
    assert out[0].startswith("BOARD (") and "not a pick" in out[-1] and no_jargon("\n".join(out)) == []
    assert slate.main(["board"]) == 0
    assert not (config.sheet_dir() / "sheet.txt").exists() and not config.bet_log_path().exists()
    assert not (config.state_dir() / "slate_sent.json").exists() and not list(config.logs_dir().iterdir())
    slate.run_alert(CHECK_AT + timedelta(minutes=1), lambda t: None, lambda m: None)
    sheet = (config.sheet_dir() / "sheet.txt").read_text()
    assert sheet.index("BOARD (") < sheet.index("TODAY (") and "OPEN PICKS" not in sheet


# Line wording on real runs ---------------------------------------------------------------------

def test_a_real_two_read_paid_run_says_the_line_moved_toward_the_pick(tmp_path, monkeypatch):
    paid(tmp_path, monkeypatch, second_priced={"R1": {"Philadelphia 76ers": D("1.62"),
                                                      "Cleveland Cavaliers": D("2.40")}})
    assert "line moved toward Cleveland, check news" in board_at(SECOND + timedelta(minutes=1))[1]


@pytest.mark.parametrize("side,expected", [("YES", D("0.40")), ("NO", D("0.60"))])
def test_earlier_fair_is_the_same_outcomes_previous_read_for_the_side_bought(side, expected):
    read = lambda at, outcome, p: {"fetched_utc": at, "source": SRC, "bookmaker": "pinnacle", "sport_key": "nba",
                                   "source_event_id": "R1", "commence_utc": START, "outcome": outcome,
                                   "fair_prob": p, "devig_method": "proportional"}
    with Store.open() as s:
        s.append("reference_odds", [read(FIRST, "Cleveland Cavaliers", 0.40),
                                    read(FIRST + timedelta(minutes=1), "Philadelphia 76ers", 0.55),
                                    read(SECOND, "Cleveland Cavaliers", 0.45)])
        ctx = slate.Context(s, SECOND, ingest=False)
        assert ctx.earlier_fair({"odds_source": SRC, "source_event_id": "R1", "ref_outcome": "Cleveland Cavaliers",
                                 "odds_fetched_utc": iso_z(SECOND), "side": side}) == expected
