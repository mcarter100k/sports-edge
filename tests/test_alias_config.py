"""config/team_aliases.json: each alias pairs its own game, inside its own sport only."""
from datetime import datetime, timezone

import pytest

from sports_edge.matching import KalshiGame, match, names_match
from sports_edge.odds import RefEventStub
from test_devig_and_matching import real_aliases

TABLES = real_aliases()
RESOLVE_BY = datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)

LAC, LAL = "KXNBAGAME-26OCT23LACLAL-LAC", "KXNBAGAME-26OCT23LACLAL-LAL"


def test_the_alias_file_is_exactly_the_reviewed_table():
    """A swapped, added or changed entry fails here (docs/specs/odds_matching_spec.md, alias batch 1)."""
    assert TABLES == {
        "Basketball": {"Los Angeles C": "Los Angeles Clippers", "Los Angeles L": "Los Angeles Lakers"},
        "Hockey": {"New York R": "New York Rangers", "New York I": "New York Islanders"},
    }


def test_every_alias_is_needed():
    for sport, table in TABLES.items():
        for kalshi, ref in table.items():
            assert names_match(kalshi, ref, table), (sport, kalshi)
            assert not names_match(kalshi, ref, {}), f"{kalshi!r} already matches without an alias"


def test_no_two_sport_tags_share_an_alias_key_with_different_targets():
    """matching.py merges a game's tag tables with dict.update; a shared key would let the last tag win silently."""
    seen = {}
    for sport, table in TABLES.items():
        for kalshi, ref in table.items():
            if kalshi in seen:
                assert seen[kalshi][1] == ref, f"{kalshi!r}: {seen[kalshi][0]} -> {seen[kalshi][1]}, {sport} -> {ref}"
            seen.setdefault(kalshi, (sport, ref))


def la_game():
    return KalshiGame("KXNBAGAME-26OCT23LACLAL", ("Basketball",), RESOLVE_BY,
                      {"Los Angeles C": LAC, "Los Angeles L": LAL})


@pytest.mark.parametrize("home,away", [("Los Angeles Lakers", "Los Angeles Clippers"),
                                       ("Los Angeles Clippers", "Los Angeles Lakers")])
def test_the_los_angeles_nba_game_pairs_through_match_with_the_real_aliases(home, away):
    stub = RefEventStub("r1", "basketball_nba", START, home, away)
    r = match([(la_game(), "Basketball")], {"Basketball": [stub]}, TABLES)
    assert {m["market_ticker"]: m["ref_outcome"] for m in r.matches} == {
        LAC: "Los Angeles Clippers", LAL: "Los Angeles Lakers"}
    assert r.unmatched == []
    without = match([(la_game(), "Basketball")], {"Basketball": [stub]}, {})
    assert without.matches == [] and without.unmatched[0]["reason"] == "name_mismatch"

