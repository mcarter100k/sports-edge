"""config/team_aliases.json: each alias pairs its own game, inside its own sport only."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sports_edge.matching import KalshiGame, match, names_match
from sports_edge.odds import RefEventStub

ALIASES = json.loads((Path(__file__).resolve().parent.parent / "config" / "team_aliases.json").read_text())
TABLES = {sport: table for sport, table in ALIASES.items() if not sport.startswith("_")}
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
    r = match([(la_game(), "Basketball")], {"Basketball": [stub]}, ALIASES)
    assert {m["market_ticker"]: m["ref_outcome"] for m in r.matches} == {
        LAC: "Los Angeles Clippers", LAL: "Los Angeles Lakers"}
    assert r.unmatched == []
    without = match([(la_game(), "Basketball")], {"Basketball": [stub]}, {})
    assert without.matches == [] and without.unmatched[0]["reason"] == "name_mismatch"


def test_a_basketball_title_never_reaches_a_football_team():
    chargers = RefEventStub("r2", "americanfootball_nfl", START, "Los Angeles Chargers", "Los Angeles Lakers")
    # even in the Basketball pool, the Basketball alias for "Los Angeles C" names the Clippers only
    r = match([(la_game(), "Basketball")], {"Basketball": [chargers]}, ALIASES)
    assert r.matches == [] and r.unmatched[0]["reason"] == "name_mismatch"
    # a Football reference game is never in a Basketball game's pool: the game is skipped, not even refused
    r = match([(la_game(), "Basketball")], {"Football": [chargers]}, ALIASES)
    assert r.matches == [] and r.unmatched == []
