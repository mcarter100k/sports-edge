"""The live slip watcher (docs/specs/slip_watch_spec.md). Fixtures are trimmed real ESPN responses of 2026-10-28."""
import copy
import json
import ssl
import urllib.error
from datetime import timedelta
from pathlib import Path

import pytest

from sports_edge import config, imessage, watch
from sports_edge.rate import parse
from sports_edge.timeutil import parse_utc

ESPN = Path(__file__).parent / "fixtures" / "espn"
BOARD = json.loads((ESPN / "scoreboard_20261028.json").read_text(encoding="utf-8"))
PRE = json.loads((ESPN / "lal_lac_pregame.json").read_text(encoding="utf-8"))
Q4 = json.loads((ESPN / "lal_lac_q4.json").read_text(encoding="utf-8"))            # 17.7 s left, LAL 103-86
MIA_FINAL = json.loads((ESPN / "mia_atl_final.json").read_text(encoding="utf-8"))  # MIA 129-117
DEN_LIVE = json.loads((ESPN / "den_nop_live.json").read_text(encoding="utf-8"))    # halftime
LAL, MIA, DEN = "401909892", "401909881", "401909889"
START = parse_utc("2026-10-29T05:00:00Z")                                            # 10:00 PM PT

# The owner's Lakers-Clippers combo of 2026-10-28 (Kessler's game left out); prices illustrative
LAKERS = ("NBA\nLA Lakers vs LA Clippers\nBrook Lopez: 1+ threes  55%\nAustin Reaves: 2+ threes  70%\n"
          "LA Lakers to win  76%\nDarius Garland: 15+ points  80%\nAustin Reaves: 20+ points  78%\n"
          "5 MARKET COMBO\n$26.99 pays $129.06\n")


def slip(*games, payout="$10 pays $50"):
    """games: (header, [legs]) -> slip text."""
    lines, n = ["NBA"], 0
    for header, legs in games:
        lines.append(header)
        for leg in legs:
            lines.append(f"{leg}  50%")
            n += 1
    return "\n".join(lines + [f"{n} MARKET COMBO", payout]) + "\n"


def final(summary, detail="Final"):
    s = copy.deepcopy(summary)
    s["header"]["competitions"][0]["status"]["type"].update(state="post", completed=True, shortDetail=detail)
    return s


def with_stat(summary, name, key, value):
    s = copy.deepcopy(summary)
    for group in s["boxscore"]["players"]:
        block = group["statistics"][0]
        for a in block["athletes"]:
            if a["athlete"]["displayName"] == name:
                a["stats"][block["names"].index(key)] = value
    return s


class Feed:
    """ESPN stand-in: the scoreboard, then each game's summaries in order (the last one repeats)."""

    def __init__(self, games, board=BOARD, fail=()):
        self.games, self.board, self.fail, self.calls = {k: list(v) for k, v in games.items()}, board, list(fail), []

    def __call__(self, url):
        self.calls.append(url)
        if "scoreboard" in url:
            return self.board
        if self.fail:
            exc = self.fail.pop(0)
            if exc:
                raise exc
        seq = self.games[url.rsplit("=", 1)[1]]
        return seq.pop(0) if len(seq) > 1 else seq[0]


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += timedelta(seconds=s)


def run(text, feed, **kw):
    out, sent, clock = [], [], Clock()
    code = watch.watch(text, fetch=feed, send=kw.pop("send", sent.append), clock=clock, sleep=clock.sleep,
                       out=out.append, **kw)
    return code, out, sent


# Pairing (never guess) ---------------------------------------------------------------------------------

def test_games_pair_by_code_or_by_name_where_espn_codes_differ():
    s = parse(slip(("WAS Wizards vs CLE Cavaliers", ["WAS Wizards to win"]), ("SA Spurs vs UTA Jazz", ["SA Spurs to win"]),
                   ("GS Warriors vs MIN Timberwolves", ["GS Warriors to win"]),
                   ("DEN Nuggets vs NOP Pelicans", ["DEN Nuggets to win"]),
                   ("LA Lakers vs LA Clippers", ["LA Lakers to win"])))
    paired, unmatched = watch.pair(s, BOARD)
    assert paired == {0: "401909882", 1: "401909890", 2: "401909888", 3: DEN, 4: LAL} and unmatched == {}


def test_a_game_not_on_the_board_or_on_it_twice_is_unmatched():
    s = parse(slip(("GS Warriors vs NY Knicks", ["GS Warriors to win"]), ("LA Lakers vs LA Clippers", ["LA Lakers to win"])))
    board = copy.deepcopy(BOARD)
    twin = copy.deepcopy(next(e for e in board["events"] if e["id"] == LAL))
    twin["id"] = "999"
    board["events"].append(twin)
    assert watch.pair(s, board) == ({}, {0: "no event", 1: "2 events"})


# Legs and states on real box scores -------------------------------------------------------------------------

def test_leg_forms():
    s = parse(slip(("LA Lakers vs LA Clippers", ["LA Lakers to win", "Austin Reaves: 20+ points", "Austin Reaves: 2+ threes",
                                                  "Jaxson Hayes: 6+ rebounds", "Luka Doncic: 8+ assists",
                                                  "Walker Kessler: 2+ blocks"])))
    legs = watch.legs_of(s, {})
    assert [(leg.kind, leg.stat, leg.need) for leg in legs] == [
        ("moneyline", None, None), ("player", "PTS", 20), ("player", "3PT", 2), ("player", "REB", 6),
        ("player", "AST", 8), (None, None, None)]
    assert (legs[-1].watched, legs[-1].detail) == (False, "form not supported")


def test_states_on_real_box_scores():
    text = slip(("LA Lakers vs LA Clippers", ["Austin Reaves: 20+ points", "Brook Lopez: 1+ threes", "LA Lakers to win",
                                              "LA Clippers to win"]),
                ("MIA Heat vs ATL Hawks", ["Klay Thompson: 3+ threes", "Davion Mitchell: 4+ assists",
                                           "Simone Fontecchio: 5+ points", "ATL Hawks to win", "MIA Heat to win",
                                           "Nobody Atall: 10+ points"]),
                ("DEN Nuggets vs NOP Pelicans", ["Nikola Jokić: 8+ assists"]))
    code, out, sent = run(text, Feed({LAL: [Q4], MIA: [MIA_FINAL], DEN: [DEN_LIVE]}), once=True)
    assert code == 0 and sent == []
    assert out[-1].splitlines()[1:] == [
        "Watching 10 of 11 legs",
        "🟡 Austin Reaves: 20+ points: 16/20 · 31 min",
        "🟡 Brook Lopez: 1+ threes: 0/1 · 22 min",
        "🟡 LA Lakers to win: leads 103-86",
        "🟡 LA Clippers to win: trails 86-103",
        "❌ Klay Thompson: 3+ threes: LOST · 1/3 · 17 min · final",
        "✅ Davion Mitchell: 4+ assists: HIT · 10/4 · 34 min",
        "⚪ Simone Fontecchio: 5+ points: DID NOT PLAY · check how Kalshi settles it",
        "❌ ATL Hawks to win: LOST · final 117-129",
        "✅ MIA Heat to win: HIT · final 129-117",
        "➖ Nobody Atall: 10+ points: not watched · UNMATCHED player",
        "🟡 Nikola Jokić: 8+ assists: 7/8 · 16 min",          # accent-free on ESPN: 'Nikola Jokic'
        "1 leg not watched"]
    assert out[-1].startswith("SLIP WATCH · 10:00 PM PT")


def test_before_tip_every_leg_waits():
    code, out, _ = run(LAKERS, Feed({LAL: [PRE]}), once=True)
    assert code == 0 and all(ln.startswith("⏳") and ln.endswith("game not started") for ln in out[-1].splitlines()[2:])


# The watch over time ---------------------------------------------------------------------------------------------

def test_the_lakers_night_start_one_message_per_change_no_repeats_then_ticket_lost():
    hit = with_stat(Q4, "Austin Reaves", "PTS", "20")
    feed = Feed({LAL: [Q4, Q4, Q4, hit, hit, final(hit)]})
    code, out, sent = run(LAKERS, feed)
    assert code == 0 and len(sent) == 3                    # start, Reaves hit, ticket lost — nothing for still polls
    assert sent[0].splitlines()[1] == "Watching 5 of 5 legs"
    assert sent[1].splitlines()[1:] == ["✅ Austin Reaves: 20+ points: HIT · 20/20 · 31 min", "OPEN:",
                                        "🟡 Brook Lopez: 1+ threes: 0/1 · 22 min",
                                        "🟡 Austin Reaves: 2+ threes: 1/2 · 31 min",
                                        "🟡 LA Lakers to win: leads 103-86",
                                        "🟡 Darius Garland: 15+ points: 14/15 · 26 min"]
    assert sent[2].splitlines()[1:] == [
        "TICKET LOST ❌", "❌ Brook Lopez: 1+ threes: LOST · 0/1 · 22 min · final",
        "❌ Austin Reaves: 2+ threes: LOST · 1/2 · 31 min · final", "✅ LA Lakers to win: HIT · final 103-86",
        "❌ Darius Garland: 15+ points: LOST · 14/15 · 26 min · final", "✅ Austin Reaves: 20+ points: HIT · 20/20 · 31 min"]
    assert sent == [m for m in out if m.startswith("SLIP WATCH")]


def test_a_hit_never_reverts_after_a_stat_correction():
    hit, corrected = with_stat(Q4, "Austin Reaves", "PTS", "20"), with_stat(Q4, "Austin Reaves", "PTS", "18")
    text = slip(("LA Lakers vs LA Clippers", ["Austin Reaves: 20+ points", "Darius Garland: 15+ points"]))
    code, _, sent = run(text, Feed({LAL: [Q4, hit, corrected, final(corrected)]}))
    assert "✅ Austin Reaves: 20+ points: HIT · 20/20 · 31 min" in sent[-1].splitlines()


def test_a_ticket_already_won_at_the_first_poll():
    code, _, sent = run(slip(("MIA Heat vs ATL Hawks", ["Davion Mitchell: 4+ assists", "MIA Heat to win"])),
                        Feed({MIA: [MIA_FINAL]}))
    assert code == 0 and [m.splitlines()[1] for m in sent] == ["Watching 2 of 2 legs", "TICKET WON ✅"]


def test_legs_not_watched_are_counted_and_keep_a_win_from_being_called_won():
    text = slip(("MIA Heat vs ATL Hawks", ["Davion Mitchell: 4+ assists", "Bam Adebayo: 2+ steals"]))
    code, _, sent = run(text, Feed({MIA: [MIA_FINAL]}))
    assert sent[-1].splitlines()[1] == "ALL WATCHED LEGS HIT — check the legs not watched or not played"
    assert sent[-1].splitlines()[-1] == "1 leg not watched"


def test_a_game_going_final_is_announced_with_its_score():
    text = slip(("LA Lakers vs LA Clippers", ["LA Lakers to win"]), ("MIA Heat vs ATL Hawks", ["Davion Mitchell: 4+ assists"]),
                ("DEN Nuggets vs NOP Pelicans", ["Nikola Jokić: 8+ assists"]))
    code, _, sent = run(text, Feed({LAL: [Q4, final(Q4)], MIA: [MIA_FINAL], DEN: [DEN_LIVE]}), max_hours=0.05)
    assert sent[1].splitlines()[1:3] == ["FINAL: LAC 86 · LAL 103", "✅ LA Lakers to win: HIT · final 103-86"]
    assert sent[-1].splitlines()[1] == "WATCH STOPPED: time limit (0.05 h)"


# Failures are loud ----------------------------------------------------------------------------------------------

def test_a_failed_first_poll_texts_and_exits_1():
    def broken(url):
        raise OSError("network is unreachable")
    code, out, sent = run(LAKERS, broken)
    assert code == 1 and sent == [out[-1]] and out[-1].splitlines()[1] == \
        "WATCH FAILED: first poll: OSError: network is unreachable"


def test_four_failed_polls_then_a_good_one_keep_going():
    feed = Feed({LAL: [Q4, final(Q4)]}, fail=[None] + [OSError("timeout")] * 4)
    code, out, sent = run(LAKERS, feed)
    assert code == 0 and [ln for ln in out if ln.startswith("POLL FAILED")] == [
        f"POLL FAILED ({k} in a row): OSError: timeout" for k in range(1, 5)]
    assert sent[-1].splitlines()[1] == "TICKET LOST ❌"


def test_five_failed_polls_in_a_row_stop_the_watch():
    code, out, sent = run(LAKERS, Feed({LAL: [Q4]}, fail=[None] + [OSError("timeout")] * 5))
    assert code == 1 and sent[-1].splitlines()[1] == \
        "WATCH FAILED: 5 polls in a row failed; stopped (OSError: timeout)"


def test_a_certificate_error_stops_at_once():
    cert = urllib.error.URLError(ssl.SSLCertVerificationError(1, "unable to get local issuer certificate"))
    code, out, sent = run(LAKERS, Feed({LAL: [Q4]}, fail=[None, cert]))
    assert code == 1 and sent[-1].splitlines()[1].startswith("WATCH FAILED: 1 poll in a row failed; stopped (URLError")


def test_a_failed_text_does_not_stop_the_watch():
    sent = []

    def flaky(msg):
        if not sent and "Watching" in msg and not getattr(flaky, "failed", False):
            flaky.failed = True
            raise imessage.SendFailed("osascript exit 1: Messages not running")
        sent.append(msg)
    code, out, _ = run(LAKERS, Feed({LAL: [Q4, final(Q4)]}), send=flaky)
    assert code == 0 and "TEXT FAILED: osascript exit 1: Messages not running" in out
    assert sent[-1].splitlines()[1] == "TICKET LOST ❌"


def test_the_time_limit_stops_the_watch():
    code, _, sent = run(LAKERS, Feed({LAL: [Q4]}), max_hours=0.1, every=60)
    assert code == 0 and sent[-1].splitlines()[1] == "WATCH STOPPED: time limit (0.1 h)"


def test_nothing_to_watch_is_a_failure_not_a_quiet_board():
    code, _, sent = run(slip(("GS Warriors vs NY Knicks", ["Stephen Curry: 15+ points"])), Feed({}))
    assert code == 1 and sent[-1].splitlines()[1] == "WATCH FAILED: no leg could be watched"


# Isolation and the command --------------------------------------------------------------------------------------

def test_once_sends_nothing_and_no_file_is_written_under_data():
    code, _, sent = run(LAKERS, Feed({LAL: [Q4]}), once=True)
    assert code == 0 and sent == []
    code = watch.watch(LAKERS, fetch=Feed({LAL: [Q4, final(Q4)]}), clock=Clock(), sleep=lambda s: None, out=lambda s: None)
    assert code == 0 and list(config.data_dir().iterdir()) == []                     # imessage.send in log mode
    assert (config.logs_dir() / "imessage.log").read_text(encoding="utf-8").count("logged-only") == 2


def test_main_exit_codes(tmp_path, capsys):
    bad, nhl, good = tmp_path / "bad.txt", tmp_path / "nhl.txt", tmp_path / "good.txt"
    bad.write_text(LAKERS.replace("5 MARKET", "4 MARKET"), encoding="utf-8")
    nhl.write_text(LAKERS.replace("NBA", "NHL"), encoding="utf-8")
    good.write_text(LAKERS, encoding="utf-8")
    assert watch.main([str(bad)]) == 2 and "Can't read the slip" in capsys.readouterr().out
    assert watch.main([str(nhl)]) == 2 and "Can't watch: league NHL is not supported" in capsys.readouterr().out
    assert watch.main([str(good), "--every", "10"]) == 2 and "at least 15 seconds" in capsys.readouterr().out


@pytest.mark.integration
def test_the_real_scoreboard_parses():
    board = watch.espn_get(f"{watch.LEAGUES['NBA']}/scoreboard?dates=20261028")
    s = parse(slip(("LA Lakers vs LA Clippers", ["LA Lakers to win"])))
    assert watch.pair(s, board)[0] == {0: LAL}


# Added after review (2026-10-28): unreadable data is a failed poll, never a zero; no silent endings ------------

def without_column(summary, key):
    s = copy.deepcopy(summary)
    for group in s["boxscore"]["players"]:
        block = group["statistics"][0]
        i = block["names"].index(key)
        block["names"] = block["names"][:i] + ["XX"] + block["names"][i + 1:]
    return s


def without_scores(summary):
    s = copy.deepcopy(summary)
    for c in s["header"]["competitions"][0]["competitors"]:
        c.pop("score", None)
    return s


REAVES_10 = slip(("LA Lakers vs LA Clippers", ["Austin Reaves: 10+ points", "Darius Garland: 15+ points"]))


@pytest.mark.parametrize("broken", [
    lambda: final(without_column(Q4, "PTS")),                     # a renamed column must not read as 0 points
    lambda: final(without_scores(Q4)),                            # a missing score must not read as 0-0
    lambda: final(with_stat(Q4, "Darius Garland", "PTS", "--")),  # a non-number must not crash or read as 0
])
def test_unreadable_data_fails_the_poll_and_never_texts_a_false_result(broken):
    code, out, sent = run(REAVES_10, Feed({LAL: [Q4] + [broken()] * 6}))
    assert code == 1 and len([ln for ln in out if ln.startswith("POLL FAILED")]) == 5
    assert sent[-1].splitlines()[1].startswith("WATCH FAILED: 5 polls in a row failed; stopped (")
    assert not any("LOST" in m or "0/10" in m for m in sent)


def test_unreadable_data_on_the_first_poll_fails_loudly():
    code, out, sent = run(REAVES_10, Feed({LAL: [without_column(Q4, "REB")]}))
    assert code == 1 and sent[-1].splitlines()[1] == "WATCH FAILED: first poll: PollFailed: box score lacks REB"


def test_a_player_found_unmatched_once_the_box_score_exists_is_texted():
    text = slip(("LA Lakers vs LA Clippers", ["Austin Reaves: 20+ points", "Nobody Atall: 10+ points"]))
    code, _, sent = run(text, Feed({LAL: [PRE, Q4, final(Q4)]}))
    assert sent[1].splitlines()[1] == "➖ Nobody Atall: 10+ points: not watched · UNMATCHED player"
    assert sent[-1].splitlines()[1] == "TICKET LOST ❌"


def test_losing_the_last_watched_leg_fails_loudly_instead_of_running_silently():
    code, _, sent = run(slip(("LA Lakers vs LA Clippers", ["Nobody Atall: 10+ points"])), Feed({LAL: [PRE, Q4]}))
    assert code == 1 and sent[-1].splitlines()[1] == "WATCH FAILED: no leg can be watched"


def test_every_game_final_with_a_leg_still_waiting_stops_the_watch():
    ended_empty = final(PRE)
    for c, score in zip(ended_empty["header"]["competitions"][0]["competitors"], ("98", "104")):
        c["score"] = score
    code, _, sent = run(slip(("LA Lakers vs LA Clippers", ["Austin Reaves: 20+ points"])), Feed({LAL: [PRE, ended_empty]}))
    assert code == 0 and sent[-1].splitlines()[1] == "WATCH STOPPED: every watched game final"
    assert sent[-1].splitlines()[2] == "⏳ Austin Reaves: 20+ points: no box score yet"


def test_a_ticket_decided_on_the_last_poll_before_the_time_limit_still_sends_its_result():
    code, _, sent = run(LAKERS, Feed({LAL: [Q4, Q4, final(Q4)]}), max_hours=2 / 60, every=60)
    assert code == 0 and sent[-1].splitlines()[1] == "TICKET LOST ❌"
    assert not any("time limit" in m for m in sent)
