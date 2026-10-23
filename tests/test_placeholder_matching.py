"""Kalshi placeholder times, match_v3 (docs/specs/odds_matching_spec.md, amendment 2026-10-22)."""
from datetime import timedelta

import pytest

from helpers import game_event, stub
from sports_edge.matching import is_placeholder, kalshi_game, match, ticker_date
from sports_edge.odds_step import games_from_db
from sports_edge.store import Store
from sports_edge.timeutil import iso_z, parse_utc

PLACEHOLDER = parse_utc("2026-10-24T03:00:00Z")      # 23:00 EDT on 2026-10-23
KICKOFF = parse_utc("2026-10-24T17:00:00Z")           # the real start, 14 h later
TEAMS = ["Akron", "Kent St."]
REF = ("Kent State Golden Flashes", "Akron Zips")      # home, away


def event(ticker, exp, teams=TEAMS):
    """A Kalshi game event whose markets all expire at `exp` (occurrence = expected)."""
    return game_event(ticker, "KXNCAAFGAME", teams, exp - timedelta(hours=3))


def game_of(ev):
    rb = min(parse_utc(m["expected_expiration_time"]) for m in ev["markets"])
    g, reason = kalshi_game(ev, ("Football",), rb)
    assert reason is None, reason
    return g


def ref(eid, commence, home=REF[0], away=REF[1]):
    return stub(eid, home, away, commence, key="americanfootball_ncaaf")


def run(ev, stubs):
    return match([(game_of(ev), "Football")], {"Football": list(stubs)}, {})


# Detection ---------------------------------------------------------------------

def test_ticker_date_reads_the_first_date_token():
    assert str(ticker_date("KXNCAAFGAME-26OCT23AKRKENT")) == "2026-10-23"
    assert str(ticker_date("KXABAGAME-26OCT231430KRKCIB")) == "2026-10-23"
    assert ticker_date("KXNCAAFGAME-AKRKENT") is None
    assert ticker_date("KXNCAAFGAME-26ZZZ23AKRKENT") is None
    assert ticker_date("KXNCAAFGAME-26FEB30AKRKENT") is None


def markets_at(exp, occ=None, n=2):
    return [{"expected_expiration_time": iso_z(exp), "occurrence_datetime": iso_z(occ or exp)} for _ in range(n)]


def test_detection_daylight_and_standard_time():
    assert is_placeholder("KXNCAAFGAME-26OCT23AKRKENT", markets_at(PLACEHOLDER))
    # 2026-11-07 is standard time: 23:00 EST = 04:00Z the next day
    assert is_placeholder("KXNCAAFGAME-26NOV07AKRKENT", markets_at(parse_utc("2026-11-08T04:00:00Z")))
    assert not is_placeholder("KXNCAAFGAME-26NOV07AKRKENT", markets_at(parse_utc("2026-11-08T03:00:00Z")))


@pytest.mark.parametrize("ticker,markets", [
    ("KXNCAAFGAME-26OCT23AKRKENT", markets_at(PLACEHOLDER + timedelta(minutes=1))),          # a minute off
    ("KXNCAAFGAME-26OCT23AKRKENT", markets_at(PLACEHOLDER, PLACEHOLDER - timedelta(hours=3))),  # occurrence differs
    ("KXNCAAFGAME-26OCT23AKRKENT", markets_at(PLACEHOLDER, n=1) +
     markets_at(PLACEHOLDER + timedelta(hours=1), n=1)),                                     # markets disagree
    ("KXNCAAFGAME-AKRKENT", markets_at(PLACEHOLDER)),                                        # no date in the ticker
    ("KXNCAAFGAME-26OCT24AKRKENT", markets_at(PLACEHOLDER)),                                 # 23:00 ET on another day
    ("KXNCAAFGAME-26OCT23AKRKENT", []),
])
def test_not_detected(ticker, markets):
    assert not is_placeholder(ticker, markets)


# The three owner-required cases ---------------------------------------------------

def test_a_placeholder_event_pairs_with_the_real_game():
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    assert game_of(ev).placeholder
    r = run(ev, [ref("R1", KICKOFF)])
    assert r.unmatched == []
    assert {(m["market_ticker"][-3:], m["ref_outcome"]) for m in r.matches} == \
        {("AKR", "Akron Zips"), ("KEN", "Kent State Golden Flashes")}
    assert all(m["source_event_id"] == "R1" for m in r.matches)


def test_a_non_placeholder_event_with_a_wrong_time_still_refuses():
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER - timedelta(hours=1))     # 02:00Z, not 23:00 ET
    assert not game_of(ev).placeholder
    r = run(ev, [ref("R1", KICKOFF)])
    assert r.matches == []
    assert r.unmatched[0]["reason"] == "no_candidate" and "placeholder" not in r.unmatched[0]["detail"]


def test_two_candidate_games_still_refuse():
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    r = run(ev, [ref("R1", KICKOFF), ref("R2", KICKOFF + timedelta(hours=6))])
    assert r.matches == []
    assert r.unmatched[0]["reason"] == "ambiguous" and "R1" in r.unmatched[0]["detail"]


# The rest of the amendment ------------------------------------------------------------

def test_a_false_detection_cannot_break_a_normal_match():
    # A genuine 8 p.m. ET start ends at 23:00 ET: it looks like a placeholder. The normal window finds
    # the same-day game, so the next-day rematch (as in an MLB series) never makes it ambiguous.
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    same_day, next_day = ref("R1", PLACEHOLDER - timedelta(hours=3)), ref("R2", PLACEHOLDER + timedelta(hours=21))
    r = run(ev, [same_day, next_day])
    assert r.unmatched == [] and {m["source_event_id"] for m in r.matches} == {"R1"}


def test_the_widened_window_has_an_end():
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    r = run(ev, [ref("R1", PLACEHOLDER + timedelta(hours=33))])
    assert r.matches == [] and r.unmatched[0]["reason"] == "no_candidate"
    assert "placeholder=1" in r.unmatched[0]["detail"]


def test_a_widened_candidate_claimed_by_another_event_refuses_both():
    a = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    b = event("KXNCAAFGAME-26OCT24AKRKENT", parse_utc("2026-10-24T20:00:00Z"))   # same teams, real time
    r = match([(game_of(a), "Football"), (game_of(b), "Football")], {"Football": [ref("R1", KICKOFF)]}, {})
    assert r.matches == [] and {u["reason"] for u in r.unmatched} == {"ambiguous"}


def test_unmatched_placeholder_counts_the_window_it_searched():
    ev = event("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER)
    other = ref("R9", KICKOFF, home="Ohio State Buckeyes", away="Penn State Nittany Lions")
    r = run(ev, [other])
    assert r.unmatched[0]["reason"] == "name_mismatch"
    assert "in_window=1" in r.unmatched[0]["detail"] and "placeholder=1" in r.unmatched[0]["detail"]


# The listing window -----------------------------------------------------------------------

def seed(ticker, exp):
    rows = []
    for t in TEAMS:
        rows.append({"ticker": f"{ticker}-{t[:3].upper()}", "event_ticker": ticker, "series_ticker": "KXNCAAFGAME",
                     "sport": "Football", "competition_scope": "Game", "yes_sub_title": t,
                     "market_type": "binary", "status": "active", "mutually_exclusive": True,
                     "expected_expiration_time": exp, "occurrence_time": exp, "close_time": exp + timedelta(days=2),
                     "resolve_by": exp, "first_seen_utc": exp, "last_seen_utc": exp})
    return rows


def test_listing_keeps_a_placeholder_event_past_its_resolve_time_but_not_an_ordinary_one():
    now = PLACEHOLDER + timedelta(hours=20)
    with Store.open() as s:
        s.upsert("series", [{"series_ticker": "KXNCAAFGAME", "title": "x", "sport": "Football",
                             "tags": '["Football"]', "first_seen_utc": now, "last_seen_utc": now}], "series_ticker")
        s.upsert("markets", seed("KXNCAAFGAME-26OCT23AKRKENT", PLACEHOLDER) +
                 seed("KXNCAAFGAME-26OCT23BAYKU", PLACEHOLDER - timedelta(hours=1)), "ticker")
        games = games_from_db(s, now, now + timedelta(hours=32))
    assert [g.event_ticker for g, _ in games] == ["KXNCAAFGAME-26OCT23AKRKENT"]
    assert games[0][0].placeholder
