from datetime import timedelta

import pytest

from sports_edge import checks, config
from sports_edge.checks import Cluster, Game, clusters, due_checks
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

T0 = parse_utc("2026-10-23T02:00:00Z")


def game(eid, minutes, key="basketball_nba"):
    return Game(eid, key, T0 + timedelta(minutes=minutes), "H", "A", f"EV-{eid}")


def test_clusters_anchor_on_the_first_game_and_span_thirty_minutes():
    cl = clusters([game("c", 30), game("a", 0), game("b", 20), game("d", 31), game("e", 50), game("f", 62)])
    assert [[g.source_event_id for g in c.games] for c in cl] == [["a", "b", "c"], ["d", "e"], ["f"]]
    assert [c.start_utc for c in cl] == [T0, T0 + timedelta(minutes=31), T0 + timedelta(minutes=62)]


def test_due_window_opens_at_the_offset_and_closes_at_each_game_start():
    c = Cluster(T0, [game("a", 0), game("b", 20)])
    at = lambda m: [(g.source_event_id, o) for _, g, o, _ in due_checks([c], {}, T0 + timedelta(minutes=m), (30,))]
    assert at(-31) == []
    assert at(-30) == [("a", 30), ("b", 30)]
    assert at(0) == [("b", 30)]                 # a has started; b is still ahead
    assert at(20) == []


def test_logged_checks_are_not_repeated_and_a_missed_offset_is_superseded():
    c = Cluster(T0, [game("a", 0)])
    now = T0 - timedelta(minutes=10)
    assert due_checks([c], {("a", 15): "checked"}, now, (60, 15)) == []
    [(_, g, run, sup)] = due_checks([c], {}, now, (60, 15))
    assert (run, sup) == (15, [60])
    [(_, g, run, sup)] = due_checks([c], {("a", 60): "checked"}, now, (60, 15))
    assert (run, sup) == (15, [])
    [(_, g, run, sup)] = due_checks([c], {}, T0 - timedelta(minutes=50), (60, 15))
    assert (run, sup) == (60, [])


def test_plan_offsets_and_credit_defaults(monkeypatch):
    assert checks.offsets() == (30,) and config.odds_daily_credits() == 15
    monkeypatch.setenv("ODDS_PLAN", "paid")
    assert checks.offsets() == (60, 15) and config.odds_daily_credits() == 600
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "40")
    assert config.odds_daily_credits() == 40
    monkeypatch.setenv("ODDS_PLAN", "pro")
    with pytest.raises(ValueError):
        checks.offsets()


def test_slate_games_use_the_latest_start_keep_scope_and_drop_conflicts():
    with Store.open() as s:
        for eid, key, ev in (("R1", "basketball_nba", "E1"), ("R2", "tennis_atp", "E2"), ("R3", "icehockey_nhl", "E3")):
            s.record_matches([{"market_ticker": f"{ev}-X", "event_ticker": ev, "source": "the-odds-api",
                               "source_event_id": eid, "ref_outcome": "X", "rule_version": "v",
                               "sport_key": key}], T0)
            s.append("reference_events", [{"fetched_utc": T0 - timedelta(hours=3), "source": "the-odds-api",
                                           "sport_key": key, "source_event_id": eid, "commence_utc": T0,
                                           "home": "H", "away": "A"}])
        s.append("reference_events", [{"fetched_utc": T0 - timedelta(hours=1), "source": "the-odds-api",
                                       "sport_key": "basketball_nba", "source_event_id": "R1",
                                       "commence_utc": T0 + timedelta(hours=2), "home": "H", "away": "A"}])
        s.append("match_conflicts", [{"market_ticker": "E3-X", "event_ticker": "E3", "source": "the-odds-api",
                                      "source_event_id": "R9", "ref_outcome": "X", "seen_utc": T0}])
        games = checks.slate_games(s, "the-odds-api", T0 - timedelta(hours=1), T0 + timedelta(hours=5))
    assert [(g.source_event_id, g.commence_utc) for g in games] == [("R1", T0 + timedelta(hours=2))]
