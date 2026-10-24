from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from helpers import FakeKalshi
from sports_edge import config, grader, ledger, mybet, slate
from sports_edge.rate import SlipError
from sports_edge.store import Store
from sports_edge.timeutil import parse_utc

SLIP = (Path(__file__).parent / "fixtures" / "slip_nba_9leg.txt").read_text(encoding="utf-8")
BET = SLIP + "placed $30 pays $1,554.40\n"
NOW = parse_utc("2026-10-23T20:00:00Z")
# (Kalshi code A, code B, ref name A, ref name B); San Antonio's ticker code is SAS, the slip says SA
GAMES = [("HOU", "SAS", "Houston Rockets", "San Antonio Spurs"), ("CLE", "PHI", "Cleveland Cavaliers", "Philadelphia 76ers"),
         ("DEN", "OKC", "Denver Nuggets", "Oklahoma City Thunder"), ("DAL", "IND", "Dallas Mavericks", "Indiana Pacers"),
         ("MIN", "ORL", "Minnesota Timberwolves", "Orlando Magic"), ("TOR", "WAS", "Toronto Raptors", "Washington Wizards")]
# player markets as Kalshi lists them (observed live 2026-10-22), in each game's points event
PLAYERS = {"HOUSAS": [("HOUKDURANT7", "Kevin Durant"), ("SASVWEMBANYAMA1", "Victor Wembanyama")],
           "DENOKC": [("DENNJOKIC15", "Nikola Jokić"), ("OKCSGILGEOUSALEXANDER2", "Shai Gilgeous-Alexander")]}


def market(ticker, event, title, event_title, series):
    return {"ticker": ticker, "event_ticker": event, "series_ticker": series, "competition_scope": "Game",
            "yes_sub_title": title, "event_title": event_title, "status": "active",
            "resolve_by": NOW + timedelta(hours=6), "first_seen_utc": NOW, "last_seen_utc": NOW}


@pytest.fixture
def world():
    """The slip's games as Kalshi game events matched to the sharp book, plus points and total-points events."""
    with Store.open() as s:
        rows, matches = [], []
        for a, b, ra, rb in GAMES:
            key = f"26OCT23{a}{b}"
            for code, ref in ((a, ra), (b, rb)):
                rows.append(market(f"KXNBAGAME-{key}-{code}", f"KXNBAGAME-{key}", code, f"{a} vs {b}", "KXNBAGAME"))
                matches.append({"market_ticker": f"KXNBAGAME-{key}-{code}", "event_ticker": f"KXNBAGAME-{key}",
                                "source": "the-odds-api", "source_event_id": f"R{a}{b}", "ref_outcome": ref,
                                "rule_version": "v", "sport_key": "basketball_nba"})
            for pid, name in PLAYERS.get(f"{a}{b}", []):
                for line in (15, 20, 25, 30):
                    for ser in ("KXNBAPTS", "KXNBAPTSPROT"):      # Kalshi lists each player market twice
                        rows.append(market(f"{ser}-{key}-{pid}-{line}", f"{ser}-{key}", f"{name}: {line}+",
                                           f"{a} vs {b}: Points", ser))
                rows.append(market(f"KXNBAPRA-{key}-{pid}-20", f"KXNBAPRA-{key}", f"{name}: 20+",
                                   f"{a} vs {b}: Total Points", "KXNBAPRA"))   # must never be taken for Points
        s.upsert("markets", rows, "ticker")
        s.record_matches(matches, NOW)
    return NOW


def logged():
    return ledger.read_rows(config.my_bets_path())


# Reading -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [SLIP, BET + "placed $30 pays $1,554.40\n", SLIP + "placed $30 pays $20\n",
                                  BET.replace("9 MARKET COMBO", "8 MARKET COMBO")])
def test_a_bet_that_cant_be_read_writes_nothing(text, world):
    with pytest.raises(SlipError):
        mybet.log_bet(text, NOW, lambda m: None)
    assert logged() == []


@pytest.mark.parametrize("text,found", [(SLIP, 0), (BET + "placed $30 pays $1,554.40\n", 2)])
def test_the_placed_line_must_appear_exactly_once(text, found):
    with pytest.raises(SlipError, match=rf"one line 'placed <cost> pays <payout>' is required \(found {found}\)"):
        mybet.split_placed(text)


def test_placed_line_forms():
    assert mybet.split_placed(SLIP + "Placed 29.93 pays 2000\n")[1:] == (D("29.93"), D("2000"))
    assert mybet.split_placed("placed $29.93 pays $2,000.00\n" + SLIP)[1:] == (D("29.93"), D("2000.00"))


# Identifying legs ---------------------------------------------------------------------------------

def test_every_leg_of_the_owners_slip_is_identified_exactly(world):
    out = []
    bet_id = mybet.log_bet(BET, NOW, out.append)
    (row,) = logged()
    assert row["leg_tickers"].split(" | ") == [
        "KXNBAPTS-26OCT23HOUSAS-HOUKDURANT7-20", "KXNBAPTS-26OCT23HOUSAS-SASVWEMBANYAMA1-20",
        "KXNBAGAME-26OCT23CLEPHI-PHI", "KXNBAGAME-26OCT23DENOKC-OKC", "KXNBAPTS-26OCT23DENOKC-DENNJOKIC15-20",
        "KXNBAPTS-26OCT23DENOKC-OKCSGILGEOUSALEXANDER2-25", "KXNBAGAME-26OCT23DALIND-IND",
        "KXNBAGAME-26OCT23MINORL-MIN", "KXNBAGAME-26OCT23TORWAS-WAS"]
    assert row["bet_id"] == bet_id and row["n_legs"] == "9" and (row["cost"], row["payout"]) == ("30", "1554.40")
    assert row["logged_utc"] == "2026-10-23T20:00:00.000Z" and row["logged_pt"] == "2026-10-23 13:00 PDT"
    # 30 / 1554.40 = 0.019300; the slip's prices multiply to 0.016441; 0.019300 / 0.016441 - 1 = +17.4%
    assert (row["implied_chance"], row["legs_product"], row["markup"]) == ("0.019300", "0.016441", "0.1739")
    assert out == ["Logged your bet " + bet_id + ": 9 legs · $30.00 pays $1,554.40 · payout's chance 1.93% · "
                   "legs multiply to 1.64% · you pay about 17% more than the legs",
                   "Games paired: KXNBAGAME-26OCT23CLEPHI, KXNBAGAME-26OCT23DALIND, KXNBAGAME-26OCT23DENOKC, "
                   "KXNBAGAME-26OCT23HOUSAS, KXNBAGAME-26OCT23MINORL, KXNBAGAME-26OCT23TORWAS",
                   "Identified all 9 legs; it will settle from Kalshi results."]


def test_ambiguous_or_missing_markets_are_not_identified(world):
    with Store.open() as s:                 # a second "Kevin Durant: 20+" points market in the same game
        s.upsert("markets", [market("KXNBAPTS-26OCT23HOUSAS-HOUKDURANT8-20", "KXNBAPTS-26OCT23HOUSAS",
                                    "Kevin Durant: 20+", "HOU vs SAS: Points", "KXNBAPTS")], "ticker")
    out = []
    mybet.log_bet(BET.replace("Shai Gilgeous-Alexander: 25+ points", "Shai Gilgeous-Alexander: 45+ points"),
                  NOW, out.append)
    tickers = logged()[0]["leg_tickers"].split(" | ")
    assert tickers[0] == "" and tickers[5] == "" and all(tickers[i] for i in (1, 2, 3, 4, 6, 7, 8))
    assert out[2] == "Identified 7 of 9 legs; legs 1, 6 not found, so this bet won't settle automatically."


def test_unknown_league_identifies_nothing_but_still_logs(world):
    out = []
    mybet.log_bet(BET.replace("NBA\n", "WNBA\n", 1), NOW, out.append)
    assert logged()[0]["leg_tickers"] == " | ".join([""] * 9)
    assert out[1] == "Games paired: none" and out[2].startswith("Identified 0 of 9 legs;")


# Settling (the grader) --------------------------------------------------------------------------

def bet_row(bet_id, tickers, cost="10", payout="100"):
    return {"bet_id": bet_id, "logged_utc": "2026-10-23T20:00:00.000Z", "logged_pt": "x", "league": "NBA",
            "n_legs": len(tickers), "legs": "x", "leg_tickers": " | ".join(tickers), "cost": cost, "payout": payout,
            "implied_chance": "0.1", "legs_product": "0.1", "markup": "0"}


def settle(results):
    with Store.open() as s:
        s.record_settlements([{"ticker": t, "result": r, "settlement_value": D("1"), "settlement_ts": NOW,
                               "source": "scan"} for t, r in results.items()], NOW)


def test_grader_settles_only_when_every_leg_has_settled_and_keeps_void_out_of_the_net():
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [
        bet_row("won", ["A", "B"]),                  # both yes
        bet_row("lost", ["C", "D"]),                 # C no, D still open: stays open until D settles
        bet_row("void", ["E", "F"]),                 # E void (stored as scalar), F yes
        bet_row("open", ["G", "H"]),                 # G yes, H open
        bet_row("stuck", ["I", ""]),                 # a leg not identified, I yes: never graded
        bet_row("stuck_lost", ["J", ""], cost="5"),  # a leg not identified, J lost: still never graded
        bet_row("void_lost", ["K", "L"])])           # K void, L open; later L no: void_leg, out of the net
    settle({"A": "yes", "B": "yes", "C": "no", "E": "scalar", "F": "yes", "G": "yes", "I": "yes", "J": "no",
            "K": "scalar"})
    out = []
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=out.append)
    got = {r["bet_id"]: (r["result"], r["dollars"]) for r in ledger.read_rows(config.my_bet_grades_path())}
    assert got == {"won": ("won", "90"), "void": ("void_leg", "")}
    assert out[-1] == ("your own bets: 7 logged; settled 1 (won 1, lost 0), +$90.00; "
                       "void leg 1; open 3; can't settle 2 (legs not identified)")
    settle({"D": "yes", "L": "no"})                  # later nights
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=out.append)
    got = {r["bet_id"]: (r["result"], r["dollars"]) for r in ledger.read_rows(config.my_bet_grades_path())}
    assert got["lost"] == ("lost", "-10") and got["void_lost"] == ("void_leg", "") and len(got) == 4
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=lambda m: None)
    assert len(ledger.read_rows(config.my_bet_grades_path())) == 4            # each bet settled once


def test_open_past_due_is_counted_and_nothing_settled_shows_no_dollars():
    with Store.open() as s:
        s.upsert("markets", [market(t, "KXNBAGAME-26OCT18XY", t, "X vs Y", "KXNBAGAME") for t in ("M", "N")], "ticker")
        s.con.execute("UPDATE markets SET resolve_by = ? WHERE ticker IN ('M', 'N')", [NOW - timedelta(days=4)])
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [bet_row("late", ["M", "N"])])
    settle({"M": "yes"})                             # N's settlement never arrived
    out = []
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=out.append)
    assert out[-1] == ("your own bets: 1 logged; settled 0 (won 0, lost 0); void leg 0; open 1 (1 past due); "
                       "can't settle 0 (legs not identified)")


def test_the_same_bet_id_twice_is_graded_once():
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [bet_row("twice", ["A"]), bet_row("twice", ["A"])])
    settle({"A": "yes"})
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=lambda m: None)
    assert [r["bet_id"] for r in ledger.read_rows(config.my_bet_grades_path())] == ["twice"]
    assert grader.my_bets_summary()[0] == 1


def test_an_unreadable_row_never_stops_the_grader():
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [bet_row("bad", ["A"], cost=""), bet_row("ok", ["B"]),
                                                                     bet_row("nan", ["A"], cost="NaN")])
    with open(config.my_bets_path(), "a", encoding="utf-8") as f:
        f.write("short,row\n")                       # a torn row: DictReader fills the rest with None
    settle({"A": "yes", "B": "no"})
    out = []
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=out.append)
    assert out[0].startswith("GRADER OK")
    assert [r["bet_id"] for r in ledger.read_rows(config.my_bet_grades_path())] == ["ok"]
    assert out[-1].endswith("; unreadable 3")


def test_morning_line_and_nothing_reaches_the_shadow_ledger_or_stage(world):
    assert slate.my_bets_line() == "Your own bets: none logged yet."
    mybet.log_bet(BET, NOW, lambda m: None)
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [bet_row("x", ["Z", ""])])
    legs = logged()[0]["leg_tickers"].split(" | ")
    settle({t: ("no" if n == 0 else "yes") for n, t in enumerate(legs)})
    grader.run_grader(FakeKalshi([], [], NOW), now_fn=lambda: NOW, out=lambda m: None)
    assert slate.my_bets_line() == "Your own bets: 2, settled 1, -$30.00. (1 can't settle)"
    data = config.shadow_ledger_path().parent
    assert not config.shadow_ledger_path().exists() and not config.shadow_grades_path().exists()
    assert not (data / "bet_log.csv").exists() and not (data / "shadow_markouts.csv").exists()
    assert grader.stage(ledger.read_rows(config.shadow_grades_path()))[1] == "Practice · 0 of 100 games with a closing price"


def test_with_nothing_settled_the_morning_line_shows_no_dollars(world):
    ledger.append_rows(config.my_bets_path(), mybet.MY_BETS_HEADER, [bet_row("x", ["Z", ""])])
    assert slate.my_bets_line() == "Your own bets: 1, settled 0. (1 can't settle)"


# Protected markets, settled games, the command's exit code ------------------------------------------

def test_protection_on_takes_the_protected_player_markets(world):
    mybet.log_bet(BET + "protection on\n", NOW, lambda m: None)
    tickers = logged()[0]["leg_tickers"].split(" | ")
    assert tickers[0] == "KXNBAPTSPROT-26OCT23HOUSAS-HOUKDURANT7-20" and tickers[2] == "KXNBAGAME-26OCT23CLEPHI-PHI"
    with pytest.raises(SlipError, match="at most one"):
        mybet.log_bet(BET + "protection on\nprotection off\n", NOW, lambda m: None)


def test_a_slip_logged_after_its_games_still_identifies_its_legs(world):
    with Store.open() as s:                 # every market settled, resolved 6 hours before logging
        s.con.execute("UPDATE markets SET status = 'finalized', resolve_by = ?", [NOW - timedelta(hours=6)])
    mybet.log_bet(BET, NOW, lambda m: None)
    assert all(logged()[0]["leg_tickers"].split(" | "))


def test_two_fitting_game_events_leave_the_legs_not_identified(world):
    with Store.open() as s:                 # the same teams again a day later (as in a series)
        s.upsert("markets", [market(f"KXNBAGAME-26OCT24CLEPHI-{c}", "KXNBAGAME-26OCT24CLEPHI", c, "CLE vs PHI",
                                    "KXNBAGAME") for c in ("CLE", "PHI")], "ticker")
    mybet.log_bet(BET, NOW, lambda m: None)
    assert logged()[0]["leg_tickers"].split(" | ")[2] == ""


@pytest.mark.parametrize("extra,code", [("placed $30 pays $1,554.40\n", 0), ("placed , pays 5\n", 2), ("", 2),
                                        ("placed $30 pays $20\n", 2)])
def test_the_command_exit_code(extra, code, world, tmp_path):
    f = tmp_path / "bet.txt"
    f.write_text(SLIP + extra, encoding="utf-8")
    assert mybet.main([str(f)]) == code


def test_a_rematch_within_7_days_refuses_the_game(world):
    """The bet's game resolved 37 h before logging; the same teams meet again 12 h after it. Never the rematch."""
    with Store.open() as s:
        s.con.execute("UPDATE markets SET status = 'finalized', resolve_by = ? WHERE event_ticker = 'KXNBAGAME-26OCT23CLEPHI'",
                      [NOW - timedelta(hours=37)])
        s.upsert("markets", [market(f"KXNBAGAME-26OCT25CLEPHI-{c}", "KXNBAGAME-26OCT25CLEPHI", c, "CLE vs PHI", "KXNBAGAME")
                             for c in ("CLE", "PHI")], "ticker")
        s.con.execute("UPDATE markets SET resolve_by = ? WHERE event_ticker = 'KXNBAGAME-26OCT25CLEPHI'",
                      [NOW + timedelta(hours=12)])
    out = []
    mybet.log_bet(BET, NOW, out.append)
    assert logged()[0]["leg_tickers"].split(" | ")[2] == "" and "CLEPHI" not in out[1]


def test_a_spread_leg_and_a_game_without_a_kalshi_event_are_not_identified(world):
    text = BET.replace("PHI 76ers to win  57%", "PHI 76ers -4.5  57%").replace("TOR Raptors vs WAS Wizards",
                                                                             "TOR Raptors vs BKN Nets")
    text = text.replace("WAS Wizards to win  34%", "BKN Nets to win  34%")
    mybet.log_bet(text, NOW, lambda m: None)
    tickers = logged()[0]["leg_tickers"].split(" | ")
    assert tickers[2] == "" and tickers[8] == "" and tickers[7] == "KXNBAGAME-26OCT23MINORL-MIN"


def test_the_sheet_carries_the_own_bets_line(world):
    slate.run_morning(parse_utc("2026-10-23T15:00:00Z"), lambda t: None, lambda *a, **k: None)
    assert "Your own bets: none logged yet." in (config.sheet_dir() / "sheet.txt").read_text()


def test_a_protected_market_without_its_normal_twin_is_not_taken(world):
    with Store.open() as s:                 # only the Protected listing of Durant 20+ remains
        s.con.execute("DELETE FROM markets WHERE ticker = 'KXNBAPTS-26OCT23HOUSAS-HOUKDURANT7-20'")
    mybet.log_bet(BET + "protection on\n", NOW, lambda m: None)
    assert logged()[0]["leg_tickers"].split(" | ")[0] == ""
