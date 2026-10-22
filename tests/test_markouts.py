"""Record-only markouts (docs/specs/grader_spec.md, "Markouts")."""
from datetime import timedelta
from decimal import Decimal as D

from helpers import FakeKalshi
from sports_edge import config, grader, ledger
from sports_edge.shadow import HEADER
from sports_edge.store import Store
from sports_edge.timeutil import iso_z, parse_utc

SRC = "the-odds-api"
START = parse_utc("2026-10-22T23:00:00Z")
T = START - timedelta(minutes=120)
TITLES = {"E1-CLE": ("E1", "Cleveland"), "E1-PHI": ("E1", "Philadelphia"), "E2-BOS": ("E2", "Boston"),
          "E2-MIA": ("E2", "Miami")}


def seed(s, rows, snaps, starts=None):
    s.upsert("markets", [{"ticker": t, "event_ticker": e, "yes_sub_title": title, "status": "finalized",
                          "first_seen_utc": T, "last_seen_utc": T} for t, (e, title) in TITLES.items()], "ticker")
    s.append("reference_events", [{"fetched_utc": T, "source": SRC, "sport_key": "nba", "source_event_id": eid,
                                   "commence_utc": at, "home": "h", "away": "a"}
                                  for eid, at in (starts or {}).items()])
    s.append("price_snapshots", [{"run_id": "x", "ts_utc": at, "tier": "near", "ticker": t, "status": "active",
                                  "yes_bid": D(b), "yes_ask": D(a)} for t, at, b, a in snaps])
    ledger.append_rows(config.shadow_ledger_path(), HEADER, rows)


def lrow(rid, market, side, at=T, mid="0.41", price="0.42", start=START):
    event, title = TITLES[market]
    return {"row_id": rid, "rule_id": "GAP_v2", "run_id": "c", "logged_utc": iso_z(at), "snapshot_utc": iso_z(at),
            "commence_utc": iso_z(start), "event_ticker": event, "market_ticker": market, "outcome": title,
            "side": side, "price_paid": price, "fee_per_contract": "0.02", "edge": "0.05", "kalshi_mid": mid,
            "source_event_id": "R" + event[1:], "odds_source": SRC, "ref_outcome": title, "fair_prob": "0.45",
            "odds_fetched_utc": iso_z(at)}


CLE = [("E1-CLE", T + timedelta(minutes=9), "0.42", "0.44"),               # too soon to be next
       ("E1-CLE", T + timedelta(minutes=15), "0.44", "0.46"),              # next: mid 0.45
       ("E1-CLE", START - timedelta(minutes=1), "0.49", "0.51"),           # close: mid 0.50
       ("E1-CLE", START - timedelta(seconds=30), "0", "0.55"),             # one-sided: never used
       ("E1-CLE", START, "0.60", "0.62")]                                  # at the start: not before it


def measured():
    return {r["row_id"]: r for r in ledger.read_rows(config.shadow_markouts_path())}


def test_markouts_are_pinned_for_yes_and_no_from_the_mid_at_entry():
    with Store.open() as s:
        seed(s, [lrow("y", "E1-CLE", "YES"), lrow("n", "E1-CLE", "NO", price="0.60")], CLE)
        assert grader.grade_markouts(s, START, SRC) == (2, 0)
    y, n = measured()["y"], measured()["n"]
    nums = lambda r, *keys: tuple(D(r[k]) for k in keys)
    assert (y["next_utc"], y["close_utc"]) == (iso_z(T + timedelta(minutes=15)), iso_z(START - timedelta(minutes=1)))
    assert nums(y, "entry_mid", "next_mid", "markout_next", "close_mid", "markout_close", "price_paid") == (
        D("0.41"), D("0.45"), D("0.04"), D("0.50"), D("0.09"), D("0.42"))
    assert nums(n, "entry_mid", "markout_next", "markout_close", "price_paid") == (
        D("0.59"), D("-0.04"), D("-0.09"), D("0.60"))


def test_the_next_price_window_includes_ten_and_twenty_minutes():
    snaps = [("E1-PHI", T + timedelta(minutes=m), "0.50", "0.52") for m in (10, 12, 60, 61)]
    with Store.open() as s:
        seed(s, [lrow("ten", "E1-PHI", "YES"), lrow("twenty", "E1-PHI", "YES", at=T + timedelta(minutes=40))], snaps)
        grader.grade_markouts(s, START, SRC)
    assert measured()["ten"]["next_utc"] == iso_z(T + timedelta(minutes=10))
    assert measured()["twenty"]["next_utc"] == iso_z(T + timedelta(minutes=60))


def test_a_row_is_measured_once_and_only_after_its_start():
    with Store.open() as s:
        seed(s, [lrow("y", "E1-CLE", "YES")], CLE)
        assert grader.grade_markouts(s, START - timedelta(seconds=1), SRC) == (0, 0)
        assert grader.grade_markouts(s, START, SRC) == (1, 0)
        assert grader.grade_markouts(s, START + timedelta(hours=1), SRC) == (0, 0)
    assert len(ledger.read_rows(config.shadow_markouts_path())) == 1


def test_postponed_rows_are_skipped_and_a_smaller_move_uses_the_later_start():
    later = START + timedelta(minutes=59)
    snaps = CLE + [("E2-BOS", START + timedelta(minutes=30), "0.70", "0.72")]
    with Store.open() as s:
        seed(s, [lrow("moved", "E2-BOS", "YES"), lrow("void", "E1-CLE", "YES")], snaps,
             {"R1": START + timedelta(minutes=60), "R2": later})
        assert grader.grade_markouts(s, later, SRC) == (1, 1)
        assert grader.markout_report(s, 1)[0].endswith("; 1 postponed rows skipped")
    m = measured()["moved"]
    assert (m["start_utc"], D(m["markout_close"])) == (iso_z(later), D("0.30"))


def test_the_report_counts_results_in_games():
    snaps = CLE + [("E1-PHI", T + timedelta(minutes=16), "0.57", "0.59"),
                   ("E2-BOS", T + timedelta(minutes=15), "0.50", "0.52")]
    rows = [lrow("y", "E1-CLE", "YES"),                                          # next +0.04
            lrow("twin", "E1-PHI", "NO", at=T + timedelta(minutes=1), mid="0.60"),   # same result, next +0.02
            lrow("b", "E2-BOS", "YES", mid="0.41")]                              # next +0.10
    with Store.open() as s:
        seed(s, rows, snaps)
        grader.grade_markouts(s, START, SRC)
        line = grader.markout_report(s, 0)[0]
    assert line.startswith("markouts (Kalshi mid of the side bought, before fee, minus the mid at entry; record only): "
                           "next price 10-20 min later +0.0700 over 2 games (INSUFFICIENT: under 20) · ")


def test_a_zero_names_its_cause():
    with Store.open() as s:
        assert grader.markout_report(s, 0) == ["markouts: 0 measured (no shadow rows logged yet)"]
        seed(s, [lrow("y", "E1-CLE", "YES")], [])
        assert grader.markout_report(s, 0) == ["markouts: 0 measured (no logged game has started yet)"]
        grader.grade_markouts(s, START, SRC)
        assert grader.markout_report(s, 0) == ["markouts: 0 measured (no later two-sided price before any start)"]


def test_grades_and_the_stage_line_are_identical_with_and_without_markouts(monkeypatch):
    def run():
        grader.run_grader(FakeKalshi([], [], START), now_fn=lambda: START + timedelta(hours=5), out=lambda m: None)
        grades = config.shadow_grades_path().read_bytes()
        return grades, grader.stage(ledger.read_rows(config.shadow_grades_path()))
    with Store.open() as s:
        seed(s, [lrow("y", "E1-CLE", "YES"), lrow("n", "E1-CLE", "NO", price="0.60")], CLE)
        s.record_settlements([{"ticker": "E1-CLE", "result": "yes", "settlement_value": D("1"),
                               "settlement_ts": START, "source": "scan"}], START)
        s.append("reference_odds", [{"fetched_utc": T + timedelta(minutes=30), "source": SRC, "bookmaker": "pinnacle",
                                     "sport_key": "nba", "source_event_id": "R1", "commence_utc": START,
                                     "outcome": "Cleveland", "fair_prob": 0.48, "devig_method": "proportional"}])
    with monkeypatch.context() as m:
        m.setattr(grader, "grade_markouts", lambda *a: (0, 0))
        m.setattr(grader, "markout_report", lambda *a: [])
        without = run()
    assert not config.shadow_markouts_path().exists()
    config.shadow_grades_path().unlink()
    with Store.open() as s:
        grader.grade_markouts(s, START, SRC)                 # the markout file exists before grading
    assert "1 of 100 games with a closing price" in without[1][1]          # the stage line has a game to count
    assert run() == without and len(measured()) == 2
