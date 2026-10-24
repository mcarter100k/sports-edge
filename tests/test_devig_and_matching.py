from datetime import timedelta
from decimal import Decimal as D

import pytest

from helpers import game_event, stub
from sports_edge.matching import check_outcomes, kalshi_game, match, names_match, normalize
from sports_edge.odds import DevigRejected, devig_proportional
from sports_edge.timeutil import parse_utc

START = parse_utc("2026-10-22T23:00:00Z")


def game(ev, tags=("Basketball",)):
    rb = min(parse_utc(m["expected_expiration_time"]) for m in ev["markets"])
    g, reason = kalshi_game(ev, tags, rb)
    assert reason is None, reason
    return g


# De-vig ---------------------------------------------------------------------

def test_devig_two_way_sums_to_one():
    fair, over = devig_proportional({"A": D("1.909"), "B": D("1.909")})
    assert fair["A"] == pytest.approx(0.5) and sum(fair.values()) == pytest.approx(1.0)
    assert over == pytest.approx(2 / 1.909)


def test_devig_three_way():
    fair, _ = devig_proportional({"A": D("2.5"), "B": D("3.0"), "Draw": D("3.4")})
    assert sum(fair.values()) == pytest.approx(1.0)
    assert fair["A"] > fair["B"] > fair["Draw"]


@pytest.mark.parametrize("outcomes", [
    {"A": D("1.0"), "B": D("5")},           # price not above 1
    {"A": D("1.5"), "B": D("1.5")},         # overround 1.33
    {"A": D("2.2"), "B": D("2.2")},         # overround 0.91 (under 1)
    {"A": D("1.9")},                        # one outcome
])
def test_devig_rejects(outcomes):
    with pytest.raises(DevigRejected):
        devig_proportional(outcomes)


# Names ----------------------------------------------------------------------

def test_normalize():
    assert normalize("  Atlético  Madrid! ") == "atletico madrid"
    assert normalize("Brighton & Hove Albion") == "brighton and hove albion"


@pytest.mark.parametrize("k,r,ok", [
    ("Cleveland", "Cleveland Cavaliers", True),
    ("Ipswich Town", "Ipswich Town", True),
    ("Los Angeles L", "Los Angeles Lakers", False),   # not a whole-word prefix
    ("Cleve", "Cleveland Cavaliers", False),
    ("", "Cleveland Cavaliers", False),
])
def test_names_match(k, r, ok):
    assert names_match(k, r, {}) is ok


def test_alias_is_needed_and_sufficient():
    assert names_match("Los Angeles L", "Los Angeles Lakers", {"Los Angeles L": "Los Angeles Lakers"})


# Moneyline shape -------------------------------------------------------------

def test_non_game_scope_rejected():
    ev = game_event("E1", "S", ["Cleveland", "Philadelphia"], START, scope="Spread")
    assert kalshi_game(ev, ("Basketball",), START)[1].startswith("not_moneyline")


def test_three_markets_without_tie_rejected():
    ev = game_event("E1", "S", ["A", "B", "C"], START)
    assert kalshi_game(ev, ("Soccer",), START)[1] == "not_moneyline:tie_shape"


def test_three_way_with_tie_accepted():
    ev = game_event("E1", "S", ["Ipswich Town", "Nottingham Forest"], START, tie=True)
    g = game(ev, ("Soccer",))
    assert g.tie_market == "E1-TIE" and set(g.teams) == {"Ipswich Town", "Nottingham Forest"}


# Matching -------------------------------------------------------------------

def nba(eid, home, away, commence=START):
    return stub(eid, home, away, commence)


def test_simple_match_maps_each_market_to_its_team():
    ev = game_event("E1", "S", ["Cleveland", "Philadelphia"], START)
    r = match([(game(ev), "Basketball")],
              {"Basketball": [nba("R1", "Philadelphia 76ers", "Cleveland Cavaliers")]}, {})
    assert r.unmatched == []
    got = {m["market_ticker"]: m["ref_outcome"] for m in r.matches}
    assert got == {"E1-CLE": "Cleveland Cavaliers", "E1-PHI": "Philadelphia 76ers"}


def test_tie_maps_to_draw():
    ev = game_event("E1", "S", ["Ipswich Town", "Nottingham Forest"], START, tie=True)
    r = match([(game(ev, ("Soccer",)), "Soccer")],
              {"Soccer": [stub("R1", "Ipswich Town", "Nottingham Forest", START, "soccer_epl")]}, {})
    assert {m["ref_outcome"] for m in r.matches} == {"Ipswich Town", "Nottingham Forest", "Draw"}


def test_outside_time_window_is_no_candidate():
    ev = game_event("E1", "S", ["Cleveland", "Philadelphia"], START)
    far = START + timedelta(days=2)
    r = match([(game(ev), "Basketball")],
              {"Basketball": [nba("R1", "Philadelphia 76ers", "Cleveland Cavaliers", far)]}, {})
    assert r.matches == [] and r.unmatched[0]["reason"] == "no_candidate"


def test_in_window_but_names_differ_is_name_mismatch():
    ev = game_event("E1", "S", ["Los Angeles L", "Boston"], START)
    r = match([(game(ev), "Basketball")],
              {"Basketball": [nba("R1", "Los Angeles Lakers", "Boston Celtics")]}, {})
    assert r.matches == [] and r.unmatched[0]["reason"] == "name_mismatch"


def test_alias_scoped_to_sport():
    ev = game_event("E1", "S", ["Los Angeles L", "Boston"], START)
    stubs = {"Basketball": [nba("R1", "Los Angeles Lakers", "Boston Celtics")]}
    assert match([(game(ev), "Basketball")], stubs,
                 {"Hockey": {"Los Angeles L": "Los Angeles Lakers"}}).matches == []
    assert len(match([(game(ev), "Basketball")], stubs,
                     {"Basketball": {"Los Angeles L": "Los Angeles Lakers"}}).matches) == 2


def test_two_reference_candidates_is_ambiguous():
    ev = game_event("E1", "S", ["New York", "Boston"], START)
    stubs = {"Basketball": [nba("R1", "New York Knicks", "Boston Celtics"),
                            nba("R2", "New York Liberty", "Boston Fever", START + timedelta(hours=1))]}
    r = match([(game(ev), "Basketball")], stubs, {})
    assert r.matches == [] and r.unmatched[0]["reason"] == "ambiguous"


def test_one_reference_claimed_by_two_kalshi_events_is_ambiguous_for_both():
    e1 = game_event("E1", "S", ["Boston", "New York"], START)
    e2 = game_event("E2", "S2", ["Boston", "New York"], START + timedelta(hours=1))
    r = match([(game(e1), "Basketball"), (game(e2), "Basketball")],
              {"Basketball": [nba("R1", "New York Knicks", "Boston Celtics", START + timedelta(minutes=30))]}, {})
    assert r.matches == []
    assert sorted(u["event_ticker"] for u in r.unmatched) == ["E1", "E2"]


def test_same_team_names_in_another_sport_do_not_collide():
    hockey = game_event("H1", "SH", ["New York", "Boston"], START)
    hoops = game_event("B1", "SB", ["New York", "Boston"], START)
    stubs = {"Hockey": [stub("RH", "New York Rangers", "Boston Bruins", START, "icehockey_nhl")],
             "Basketball": [nba("RB", "New York Knicks", "Boston Celtics")]}
    r = match([(game(hockey, ("Hockey",)), "Hockey"), (game(hoops, ("Basketball",)), "Basketball")],
              stubs, {})
    pairs = {(m["event_ticker"], m["source_event_id"]) for m in r.matches}
    assert pairs == {("H1", "RH"), ("B1", "RB")} and r.unmatched == []


def test_unpolled_sport_is_not_reported_unmatched():
    ev = game_event("E1", "S", ["A", "B"], START)
    r = match([(game(ev, ("Esports",)), "Esports")], {"Basketball": []}, {})
    assert r.matches == [] and r.unmatched == []


def test_outcome_count_rule():
    assert check_outcomes(["A Team", "B Team"], ["A Team", "B Team"]) is None
    assert "outcome_count" in check_outcomes(["A Team", "B Team"], ["A Team", "B Team", "Draw"])
    assert "outcome_count" in check_outcomes(["A Team", "B Team", "Draw"], ["A Team", "B Team"])


# Power de-vig ---------------------------------------------------------------

def test_power_sums_to_one_and_shifts_toward_the_favourite():
    from sports_edge.odds import devig_power
    odds = {"Fav": D("1.30"), "Dog": D("3.60")}
    power = devig_power(odds)
    prop, _ = devig_proportional(odds)
    assert sum(power.values()) == pytest.approx(1.0, abs=1e-9)
    assert power["Fav"] > prop["Fav"] and power["Dog"] < prop["Dog"]


def test_power_equals_proportional_without_margin():
    from sports_edge.odds import devig_power
    odds = {"A": D("2.0"), "B": D("2.0")}      # implied 0.5 + 0.5: no margin
    prop, over = devig_proportional(odds)
    assert over == pytest.approx(1.0)
    assert devig_power(odds)["A"] == pytest.approx(prop["A"], abs=1e-9)


def test_power_three_way():
    from sports_edge.odds import devig_power
    p = devig_power({"A": D("2.5"), "B": D("3.0"), "Draw": D("3.4")})
    assert sum(p.values()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("outcomes", [{"A": D("1.0"), "B": D("5")}, {"A": D("1.5"), "B": D("1.5")}])
def test_power_shares_the_rejections(outcomes):
    from sports_edge.odds import devig_power
    with pytest.raises(DevigRejected):
        devig_power(outcomes)


# Owner-approved after the live odds run: "St." = "State", NHL New York aliases (match_v2) -------

def real_aliases():
    from sports_edge import config
    return {k: v for k, v in config.load_json_config("team_aliases.json").items() if not k.startswith("_")}


def ref_game(eid, home, away, key):
    return stub(eid, home, away, START, key)


def test_st_reads_as_state_and_pairs_the_right_game():
    assert normalize("Appalachian St.") == "appalachian state" and normalize("St. Louis") == "state louis"
    ev = game_event("KXNCAAFGAME-JMUAPP", "S", ["Appalachian St.", "James Madison"], START)
    other = ref_game("R9", "Appalachian Campus Club", "James Madison Dukes", "americanfootball_ncaaf")
    r = match([(game(ev, ("Football",)), "Football")],
              {"Football": [ref_game("R1", "Appalachian State Mountaineers", "James Madison Dukes",
                                     "americanfootball_ncaaf"), other]}, real_aliases())
    assert {m["market_ticker"]: (m["source_event_id"], m["ref_outcome"]) for m in r.matches} == {
        "KXNCAAFGAME-JMUAPP-APP": ("R1", "Appalachian State Mountaineers"),
        "KXNCAAFGAME-JMUAPP-JAM": ("R1", "James Madison Dukes")}


def test_st_louis_still_pairs():
    ev = game_event("KXNHLGAME-VGKSTL", "S", ["Vegas", "St. Louis"], START)
    r = match([(game(ev, ("Hockey",)), "Hockey")],
              {"Hockey": [ref_game("R1", "St Louis Blues", "Vegas Golden Knights", "icehockey_nhl")]}, real_aliases())
    assert {m["ref_outcome"] for m in r.matches} == {"St Louis Blues", "Vegas Golden Knights"}


@pytest.mark.parametrize("kalshi,ref_home,ref_away,want", [
    (["New York R", "Toronto"], "Toronto Maple Leafs", "New York Rangers", "New York Rangers"),
    (["Los Angeles", "New York I"], "New York Islanders", "Los Angeles Kings", "New York Islanders"),
])
def test_new_york_aliases_pair_the_right_game(kalshi, ref_home, ref_away, want):
    ev = game_event("KXNHLGAME-NY", "S", kalshi, START)
    # both New York teams play tonight: the alias must pick the right one, never both
    stubs = [ref_game("R1", ref_home, ref_away, "icehockey_nhl"),
             ref_game("R2", "Boston Bruins", "New York Islanders" if "Rangers" in want else "New York Rangers",
                      "icehockey_nhl")]
    r = match([(game(ev, ("Hockey",)), "Hockey")], {"Hockey": stubs}, real_aliases())
    assert r.unmatched == [] and {m["source_event_id"] for m in r.matches} == {"R1"}
    assert want in {m["ref_outcome"] for m in r.matches}


def test_without_the_alias_new_york_r_still_refuses():
    ev = game_event("KXNHLGAME-NYRTOR", "S", ["New York R", "Toronto"], START)
    r = match([(game(ev, ("Hockey",)), "Hockey")],
              {"Hockey": [ref_game("R1", "Toronto Maple Leafs", "New York Rangers", "icehockey_nhl")]}, {})
    assert r.matches == [] and r.unmatched[0]["reason"] == "name_mismatch"


def test_an_ambiguous_name_still_refuses():
    # "Kansas" fits both "Kansas Jayhawks" and "Kansas State Wildcats": each Kalshi team must fit exactly one
    ev = game_event("KXNCAAFGAME-KUKSU", "S", ["Kansas", "Kansas St."], START)
    r = match([(game(ev, ("Football",)), "Football")],
              {"Football": [ref_game("R1", "Kansas State Wildcats", "Kansas Jayhawks", "americanfootball_ncaaf")]},
              real_aliases())
    assert r.matches == [] and r.unmatched[0]["reason"] == "name_mismatch"


def test_matches_record_rule_version_match_v3():
    from sports_edge.matching import RULE_VERSION
    assert RULE_VERSION == "match_v3"
