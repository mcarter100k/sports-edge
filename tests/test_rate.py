from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from helpers import fee_table
from sports_edge import fees, rate
from sports_edge.rate import NO_CREDITS, SlipError, grade, parse
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

FIXTURE = (Path(__file__).parent / "fixtures" / "slip_nba_9leg.txt").read_text(encoding="utf-8")
NOW = parse_utc("2026-10-23T22:00:00Z")
JARGON = ("clv", "brier", "de-vig", "devig", "overround", " ev ", "edge", "value")
# Kalshi codes and names as observed live on 2026-10-22 (San Antonio's ticker code is SAS, the slip says SA)
GAMES = [("HOU", "SAS", "Houston", "San Antonio", "Houston Rockets", "San Antonio Spurs", 0.40),
         ("CLE", "PHI", "Cleveland", "Philadelphia", "Cleveland Cavaliers", "Philadelphia 76ers", 0.45),
         ("DEN", "OKC", "Denver", "Oklahoma City", "Denver Nuggets", "Oklahoma City Thunder", 0.20),
         ("DAL", "IND", "Dallas", "Indiana", "Dallas Mavericks", "Indiana Pacers", 0.30),
         ("MIN", "ORL", "Minnesota", "Orlando", "Minnesota Timberwolves", "Orlando Magic", 0.62),
         ("TOR", "WAS", "Toronto", "Washington", "Toronto Raptors", "Washington Wizards", 0.66)]


def world(read_age=timedelta(minutes=5), start_in=timedelta(hours=3)):
    """The fixture's games as Kalshi events matched to sharp-book games, read `read_age` ago."""
    with Store.open() as s:
        markets, matches, refs, events = [], [], [], []
        for a, b, ka, kb, ra, rb, fair_a in GAMES:
            ev = f"KXNBAGAME-26OCT23{a}{b}"
            for code, title, ref, fair in ((a, ka, ra, fair_a), (b, kb, rb, 1 - fair_a)):
                ticker = f"{ev}-{code}"
                markets.append({"ticker": ticker, "event_ticker": ev, "series_ticker": "KXNBAGAME",
                                "sport": "Basketball", "competition_scope": "Game", "yes_sub_title": title,
                                "status": "active", "resolve_by": NOW + start_in + timedelta(hours=3),
                                "fee_type": "quadratic", "fee_multiplier": 1, "fee_override": False,
                                "first_seen_utc": NOW, "last_seen_utc": NOW})
                matches.append({"market_ticker": ticker, "event_ticker": ev, "source": "the-odds-api",
                                "source_event_id": f"R{a}{b}", "ref_outcome": ref, "rule_version": "v",
                                "sport_key": "basketball_nba"})
                refs.append({"fetched_utc": NOW - read_age, "source": "the-odds-api", "bookmaker": "pinnacle",
                             "sport_key": "basketball_nba", "source_event_id": f"R{a}{b}",
                             "commence_utc": NOW + start_in, "outcome": ref, "fair_prob": fair,
                             "devig_method": "proportional"})
            events.append({"fetched_utc": NOW - timedelta(hours=1), "source": "the-odds-api",
                           "sport_key": "basketball_nba", "source_event_id": f"R{a}{b}",
                           "commence_utc": NOW + start_in, "home": rb, "away": ra})
        s.upsert("markets", markets, "ticker")
        s.record_matches(matches, NOW)
        s.append("reference_odds", refs)
        s.append("reference_events", events)


class Pull:
    """A one-call odds source: records its call for the budget, returns `fairs` as decimal odds."""
    name = "the-odds-api"

    def __init__(self, store, fairs):
        self.store, self.fairs, self.calls = store, fairs, []

    def fetch_moneylines(self, key, ids, now):
        from sports_edge.odds import RefEvent
        self.calls.append((key, list(ids)))
        self.store.append("odds_calls", [{"ts_utc": now, "sport_key": key, "endpoint": "odds",
                                          "http_status": 200, "credits_last": 1}])
        out = []
        for eid in ids:
            game = next(g for g in GAMES if f"R{g[0]}{g[1]}" == eid)
            fa = self.fairs.get(eid, game[6])
            out.append(RefEvent(eid, key, NOW, game[5], game[4], "pinnacle", NOW, now,
                                {game[4]: D(str(round(1 / (fa * 1.02), 6))),        # a 2% bookmaker margin
                                 game[5]: D(str(round(1 / ((1 - fa) * 1.02), 6)))}))
        return out


def run(source=None, now=NOW):
    out, made = [], {}

    def factory(store):
        made["src"] = source(store) if source else None
        return made["src"]

    rate.rate(FIXTURE, now, factory, out.append)
    return out, made.get("src")


@pytest.fixture
def odds_on(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "k")


# Reading the slip --------------------------------------------------------------------------

def test_the_owners_slip_is_read_exactly():
    slip = parse(FIXTURE)
    assert slip.league == "NBA" and len(slip.games) == 6 and len(slip.legs) == slip.count == 9
    assert (slip.stake, slip.payout) == ("30", "1,554.40")
    assert slip.games[0] == ("HOU Rockets", "SA Spurs")
    assert [leg.team for leg in slip.legs if leg.team] == [
        "PHI 76ers", "OKC Thunder", "IND Pacers", "MIN Timberwolves", "WAS Wizards"]
    assert slip.legs[4].text == "Nikola Jokić: 20+ points" and slip.legs[4].pct == "77"
    assert [leg.game for leg in slip.legs] == [0, 0, 1, 2, 2, 2, 3, 4, 5]


def test_count_and_payout_may_share_a_line():
    one_line = FIXTURE.replace("9 MARKET COMBO\n$30 pays $1,554.40", "9 MARKET COMBO  $30 pays $1,554.40")
    assert parse(one_line).payout == "1,554.40"


@pytest.mark.parametrize("change", [
    lambda t: t.replace("NBA\nHOU Rockets vs SA Spurs\n", "NBA\nKevin Durant: 20+ points  74%\nHOU Rockets vs SA Spurs\n"),
    lambda t: t.replace("PHI 76ers to win  57%", "BOS Celtics to win  57%"),
    lambda t: t.replace("9 MARKET COMBO", "8 MARKET COMBO"),
    lambda t: t.replace("$30 pays $1,554.40\n", ""),
    lambda t: t.replace("74%", "0%", 1),
    lambda t: t.replace("74%", "100%", 1),
    lambda t: t + "extra line\n",
    lambda t: t.replace("Kevin Durant: 20+ points  74%", "Kevin Durant: 20+ points"),
    lambda t: "NBA\n" + t,
])
def test_anything_off_format_stops_without_guessing(change):
    with pytest.raises(SlipError):
        parse(change(FIXTURE))


def test_main_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "slip.txt", tmp_path / "bad.txt"
    good.write_text(FIXTURE, encoding="utf-8")
    bad.write_text(FIXTURE.replace("9 MARKET", "8 MARKET"), encoding="utf-8")
    assert rate.main([str(good)]) == 0 and capsys.readouterr().out.startswith("Slip: 9 legs · $30 pays $1,554.40")
    assert rate.main([str(bad)]) == 2
    assert capsys.readouterr().out.startswith("Can't read the slip: the combo line must give the number of legs")


# The markup line (always printed) ---------------------------------------------------------------

def test_markup_line_on_the_owners_slip():
    out, _ = run()
    # 30 / 1554.40 = 1.930%; .74 x .70 x .57 x .74 x .77 x .78 x .67 x .55 x .34 = 1.644%; 1/0.01644 = 60.8
    assert out[10] == ("Markup: the payout's chance is 1.93%; the legs' own prices multiply to 1.64%, so you pay "
                       "about 17% more than the legs. You'd win about 1 in 61.")
    assert out[3] == "3. PHI 76ers to win · 57% · no sharp price (odds not set up)"
    assert out[1] == "1. Kevin Durant: 20+ points · 74% · no sharp price (not a moneyline)"


# Pairing, fair chances and grades ------------------------------------------------------------------

@pytest.mark.parametrize("d,letter", [("0.05", "A"), ("0.0499", "B"), ("0.03", "B"), ("0.0299", "C"), ("0", "C"),
                                      ("-0.0001", "D"), ("-0.0299", "D"), ("-0.03", "F"), ("-0.2", "F")])
def test_grade_bands(d, letter):
    assert grade(D(d)) == letter


def legs_after_fee(fairs):
    """Expected gap after fee per moneyline leg, from the real fee function."""
    out = {}
    for n, (price, fair) in fairs.items():
        p = D(price)
        out[n] = D(str(fair)).quantize(D("0.000001")) - p - fees.fee_per_contract(p, int(D(10) // p), 1, "taker",
                                                                                  D("0.0001"))
    return out


def test_paired_legs_get_fair_chances_and_grades_after_fee(odds_on, tmp_path):
    fee_table(tmp_path)
    world()
    out, src = run()
    assert src is None                                     # every read was fresh: no pull, no credit
    gaps = legs_after_fee({3: ("0.57", 0.55), 4: ("0.74", 0.80), 7: ("0.67", 0.70), 8: ("0.55", 0.62),
                           9: ("0.34", 0.34)})
    assert [grade(gaps[n]) for n in (3, 4, 7, 8, 9)] == ["F", "B", "C", "A", "D"]
    for n, letter in zip((3, 4, 7, 8, 9), "FBCAD"):
        gap = gaps[n]
        assert out[n].endswith(f"· {abs(gap) * 100:.1f}¢ {'under' if gap >= 0 else 'over'} fair after fee · {letter}")
    assert out[3].startswith("3. PHI 76ers to win · 57% · fair 55.0% · ")
    assert out[11] == ("Combo: not fair-priced — 4 legs without a sharp price; same-game legs: "
                       "HOU Rockets vs SA Spurs (2), DEN Nuggets vs OKC Thunder (3).")
    assert not any(w in " ".join(out).lower() for w in JARGON)


def test_sa_spurs_pairs_by_name_and_two_fitting_games_pair_neither(odds_on, tmp_path):
    fee_table(tmp_path)
    world()
    slip = parse("NBA\nHOU Rockets vs SA Spurs\nSA Spurs to win  60%\n1 MARKET COMBO\n$10 pays $16\n")
    with Store.open() as s:
        paired = rate.pair_games(s, slip, ["KXNBAGAME"], NOW)
    assert paired[0]["SA Spurs"][0] == "KXNBAGAME-26OCT23HOUSAS-SAS"      # code SA != SAS; name Spurs fits
    assert paired[0]["HOU Rockets"][0] == "KXNBAGAME-26OCT23HOUSAS-HOU"
    with Store.open() as s:                                # a second open HOU-SAS game in the window
        s.upsert("markets", [{"ticker": f"KXNBAGAME-26OCT24HOUSAS-{c}", "event_ticker": "KXNBAGAME-26OCT24HOUSAS",
                              "series_ticker": "KXNBAGAME", "competition_scope": "Game", "yes_sub_title": t,
                              "status": "active", "resolve_by": NOW + timedelta(hours=30),
                              "first_seen_utc": NOW, "last_seen_utc": NOW}
                             for c, t in (("HOU", "Houston"), ("SAS", "San Antonio"))], "ticker")
        s.record_matches([{"market_ticker": f"KXNBAGAME-26OCT24HOUSAS-{c}", "event_ticker": "KXNBAGAME-26OCT24HOUSAS",
                           "source": "the-odds-api", "source_event_id": "R2", "ref_outcome": r, "rule_version": "v",
                           "sport_key": "basketball_nba"}
                          for c, r in (("HOU", "Houston Rockets"), ("SAS", "San Antonio Spurs"))], NOW)
        assert rate.pair_games(s, slip, ["KXNBAGAME"], NOW) == {}       # never guess between two


def test_an_old_read_triggers_one_pull_per_sport_and_is_not_stored(odds_on, tmp_path):
    fee_table(tmp_path)
    world(read_age=timedelta(minutes=16))
    with Store.open() as s:
        before = s.con.execute("SELECT count(*) FROM reference_odds").fetchone()[0]
    out, src = run(lambda st: Pull(st, {}))
    assert len(src.calls) == 1 and src.calls[0][0] == "basketball_nba"
    assert sorted(src.calls[0][1]) == ["RCLEPHI", "RDALIND", "RDENOKC", "RMINORL", "RTORWAS"]
    assert out[3].startswith("3. PHI 76ers to win · 57% · fair 55.0%")
    with Store.open() as s:
        assert s.con.execute("SELECT count(*) FROM reference_odds").fetchone()[0] == before   # never a stored read
        assert s.con.execute("SELECT count(*) FROM odds_calls").fetchone()[0] == 1            # but it costs a credit


def test_no_credits_left_says_cant_rate(odds_on, tmp_path, monkeypatch):
    fee_table(tmp_path)
    monkeypatch.setenv("ODDS_DAILY_CREDITS", "0")
    world(read_age=timedelta(minutes=16))
    out, src = run(lambda st: Pull(st, {}))
    assert src.calls == [] and out[-1] == NO_CREDITS == "Can't rate: no odds credits left today"
    assert out[3] == "3. PHI 76ers to win · 57% · no sharp price (odds over 15 min old, no credits left)"
    assert out[10].startswith("Markup: ")                              # always printed


def test_without_a_fee_table_the_fair_chance_shows_but_no_grade(odds_on):
    world()
    out, _ = run()
    assert out[3] == "3. PHI 76ers to win · 57% · fair 55.0% · no grade (fee not confirmed (no fee table))"


def test_a_started_game_is_not_rated(odds_on, tmp_path):
    fee_table(tmp_path)
    world(start_in=timedelta(minutes=-10))
    out, _ = run()
    assert out[3] == "3. PHI 76ers to win · 57% · game started — not rated"


def test_no_matched_games_for_the_league_is_named(odds_on):
    out, _ = run()
    assert out[3] == "3. PHI 76ers to win · 57% · not paired (no NBA games paired with the sharp book yet)"


def test_combo_fair_line_when_every_leg_is_a_priced_moneyline_from_different_games(odds_on, tmp_path):
    fee_table(tmp_path)
    world()
    text = ("NBA\nCLE Cavaliers vs PHI 76ers\nPHI 76ers to win  57%\nMIN Timberwolves vs ORL Magic\n"
            "MIN Timberwolves to win  55%\n2 MARKET COMBO\n$10 pays $31.50\n")
    out = []
    rate.rate(text, NOW, lambda st: None, out.append)
    # 0.55 x 0.62 = 34.10%; the payout's chance is 10 / 31.50 = 31.75%
    assert out[-1] == ("Combo: the legs' fair chances multiply to 34.10% against the payout's 31.75%, "
                       "before fee (combo fee unconfirmed, so no grade).")


def test_rate_pairs_only_open_games_still_to_resolve_by_default():
    """mybet widens pair_games with before= and any_status=; rate's own defaults must not change."""
    from sports_edge import rate
    slip = rate.parse("NBA\nCLE Cavaliers vs PHI 76ers\nPHI 76ers to win  57%\n1 MARKET COMBO\n$10 pays $17.50\n")
    now = parse_utc("2026-10-23T20:00:00Z")
    with Store.open() as s:
        rows = [{"ticker": f"KXNBAGAME-26OCT23CLEPHI-{c}", "event_ticker": "KXNBAGAME-26OCT23CLEPHI",
                 "series_ticker": "KXNBAGAME", "competition_scope": "Game", "yes_sub_title": c, "event_title": "CLE vs PHI",
                 "status": "active", "resolve_by": now + timedelta(hours=6), "first_seen_utc": now, "last_seen_utc": now}
                for c in ("CLE", "PHI")]
        s.upsert("markets", rows, "ticker")
        assert rate.pair_games(s, slip, ["KXNBAGAME"], now)                       # open and ahead: pairs
        s.con.execute("UPDATE markets SET status = 'finalized'")
        assert rate.pair_games(s, slip, ["KXNBAGAME"], now) == {}                 # finalized: refused
        s.con.execute("UPDATE markets SET status = 'active', resolve_by = ?", [now - timedelta(hours=6)])
        assert rate.pair_games(s, slip, ["KXNBAGAME"], now) == {}                 # already resolved: refused
