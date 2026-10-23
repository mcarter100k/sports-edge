"""research/studies/match_coverage.py on a seeded database."""
from datetime import datetime, timedelta, timezone

from research.studies import match_coverage
from sports_edge.store import Store

T = datetime(2026, 10, 22, 2, 0, tzinfo=timezone.utc)


def mk(event, series, ticker):
    return {"ticker": ticker, "event_ticker": event, "series_ticker": series, "sport": "Basketball",
            "close_time": T + timedelta(hours=3), "expected_expiration_time": T + timedelta(hours=3),
            "first_seen_utc": T, "last_seen_utc": T}


def seed():
    with Store.open() as s:
        s.upsert("markets", [mk("NBA-A", "KXNBAGAME", "NBA-A-X"), mk("NBA-B", "KXNBAGAME", "NBA-B-X"),
                             mk("NBA-C", "KXNBAGAME", "NBA-C-X"), mk("ABA-A", "KXABAGAME", "ABA-A-X")],
                 "ticker")
        s.record_matches([{"market_ticker": "NBA-A-X", "event_ticker": "NBA-A", "source": "the-odds-api",
                           "source_event_id": "r1", "ref_outcome": "X", "rule_version": "v",
                           "sport_key": "basketball_nba"}], T)
        s.append("unmatched", [
            {"run_id": "1", "ts_utc": T, "event_ticker": "NBA-B", "sport": "Basketball", "reason": "no_candidate",
             "detail": "teams=['P', 'Q'] in_window=0"},
            {"run_id": "2", "ts_utc": T + timedelta(hours=1), "event_ticker": "NBA-B", "sport": "Basketball",
             "reason": "name_mismatch", "detail": "teams=['Los Angeles C', 'Q'] in_window=1"},
            {"run_id": "1", "ts_utc": T, "event_ticker": "NBA-A", "sport": "Basketball", "reason": "no_candidate",
             "detail": "matched later"},
            {"run_id": "1", "ts_utc": T, "event_ticker": "NBA-C", "sport": "Basketball", "reason": "no_candidate",
             "detail": "x"},
            {"run_id": "1", "ts_utc": T, "event_ticker": "ABA-A", "sport": "Basketball", "reason": "name_mismatch",
             "detail": "y"},
            {"run_id": "1", "ts_utc": T, "event_ticker": "ref:zz", "sport": "basketball_nba",
             "reason": "no_kalshi_event", "detail": "z"}])
        s.append("reference_events", [
            {"fetched_utc": T, "source": "the-odds-api", "sport_key": "basketball_nba", "source_event_id": "r2",
             "commence_utc": T, "home": "LA Clippers", "away": "Q Team"},
            {"fetched_utc": T, "source": "the-odds-api", "sport_key": "basketball_nba", "source_event_id": "r3",
             "commence_utc": T - timedelta(hours=9), "home": "Too Early", "away": "Team"}])


def test_report_counts_latest_reason_and_lists_window_candidates():
    seed()
    with Store.open(read_only=True) as s:
        text = match_coverage.report(s.con)
    row = next(line for line in text.splitlines() if line.startswith("KXNBAGAME"))
    assert row.split()[2:5] == ["1", "2", "33%"]
    assert "name_mismatch=1" in row and "no_candidate=1" in row
    assert "KXABAGAME" not in text.split("Kalshi games in series that have never paired")[0]
    assert "  Basketball: 1" in text
    assert "basketball_nba: 1" in text
    assert "reference in window: LA Clippers vs Q Team" in text
    assert "Too Early" not in text
