"""config/team_aliases.json: each alias pairs its own game, inside its own sport only."""
import json
from datetime import datetime, timezone
from pathlib import Path

from sports_edge.matching import KalshiGame, candidates, names_match
from sports_edge.odds import RefEventStub

ALIASES = json.loads((Path(__file__).resolve().parent.parent / "config" / "team_aliases.json").read_text())
START = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)


def test_every_alias_matches_its_reference_name_in_its_sport():
    for sport, table in ALIASES.items():
        if sport.startswith("_"):
            continue
        for kalshi, ref in table.items():
            assert names_match(kalshi, ref, table), (sport, kalshi)
            assert not names_match(kalshi, ref, {}), f"{kalshi!r} already matches without an alias"


def test_shared_city_aliases_never_cross_sports():
    assert not names_match("Los Angeles C", "Los Angeles Chargers", ALIASES["Basketball"])
    assert not names_match("Los Angeles C", "Los Angeles Clippers", ALIASES["Football"])


def test_the_los_angeles_nba_game_pairs_with_the_alias_and_not_without():
    game = KalshiGame("KXNBAGAME-26OCT23LACLAL", ("Basketball",), START.replace(hour=5),
                      {"Los Angeles C": "KXNBAGAME-26OCT23LACLAL-LAC", "Los Angeles L": "KXNBAGAME-26OCT23LACLAL-LAL"})
    stub = RefEventStub("r1", "basketball_nba", START, "Los Angeles Lakers", "Los Angeles Clippers")
    (found,) = candidates(game, [stub], ALIASES["Basketball"])
    assert found[1] == {"Los Angeles C": "Los Angeles Clippers", "Los Angeles L": "Los Angeles Lakers"}
    assert candidates(game, [stub], {}) == []
