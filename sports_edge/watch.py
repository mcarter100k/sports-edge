"""Watch a slip live and text each change (docs/specs/slip_watch_spec.md). Facts only.

python -m sports_edge.watch slip.txt [--every 30] [--max-hours 6] [--date 2026-10-28] [--no-text] [--once]

Reads the same slip as the rater, pairs its games with ESPN's public scoreboard and its players with
the box score without guessing, and texts the owner when a leg hits or misses, a game ends, or the
ticket is decided. It never calls Kalshi, never suggests a pick, hedge or cash-out, and writes
nothing under data/ (its only write is imessage.send's log line).
"""
import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date as Date, timedelta

from . import imessage, net
from .matching import normalize
from .rate import TEAM, SlipError, parse
from .timeutil import display_clock, display_date, utcnow

LEAGUES = {"NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"}
PROP = re.compile(r"(?P<name>.+?):\s+(?P<need>\d+)\+\s+(?P<stat>points|threes|rebounds|assists)")
STATS = {"points": "PTS", "threes": "3PT", "rebounds": "REB", "assists": "AST"}
NEEDED = ("MIN",) + tuple(STATS.values())
SOCKET_S = 30
FAIL_LIMIT = 5
MIN_EVERY = 15
OPEN = ("waiting", "live")
DONE = ("HIT", "LOST", "DID NOT PLAY")
ICON = {"HIT": "✅", "LOST": "❌", "DID NOT PLAY": "⚪", "live": "🟡", "waiting": "⏳", "not watched": "➖"}


class Unsupported(Exception):
    pass


class PollFailed(Exception):
    pass


def espn_get(url):
    status, _, body = net.https_get(url, timeout=SOCKET_S)
    if status != 200:
        raise PollFailed(f"HTTP {status}")
    return json.loads(body)


# Reading ESPN --------------------------------------------------------------------------------------

def team_fits(slip_team, abbr, name):
    """Code equals ESPN's abbreviation, or the normalized name equals ESPN's team name."""
    m = TEAM.fullmatch(slip_team)
    return bool(m) and (m["code"] == abbr or normalize(m["name"]) == normalize(name or ""))


def teams_of(competition):
    return [(c["team"]["abbreviation"], c["team"].get("name") or "") for c in competition["competitors"]]


def pair(slip, scoreboard):
    """({game index: event id}, {game index: reason}) — a game pairs only with exactly one event."""
    events = [(e["id"], teams_of(e["competitions"][0])) for e in scoreboard.get("events", [])]
    paired, unmatched = {}, {}
    for g, (a, b) in enumerate(slip.games):
        found = [eid for eid, ts in events if len(ts) == 2 and any(
            team_fits(a, *x) and team_fits(b, *y) for x, y in ((ts[0], ts[1]), (ts[1], ts[0])))]
        if len(found) == 1:
            paired[g] = found[0]
        else:
            unmatched[g] = "no event" if not found else f"{len(found)} events"
    return paired, unmatched


def read_game(summary):
    """The game as read now. Anything that can't be read fails the poll; it is never taken as a zero."""
    comp = summary["header"]["competitions"][0]
    state = comp["status"]["type"]["state"]
    teams = []
    for (abbr, name), c in zip(teams_of(comp), comp["competitors"]):
        if c.get("score") in (None, "") and state != "pre":
            raise PollFailed(f"no score for {abbr}")
        teams.append((abbr, name, int(c.get("score") or 0)))
    players = {}
    for group in (summary.get("boxscore") or {}).get("players") or []:
        block = group["statistics"][0]
        missing = [k for k in NEEDED if k not in block["names"]]
        if missing:
            raise PollFailed(f"box score lacks {', '.join(missing)}")
        for a in block["athletes"]:
            stats = a.get("stats") or []
            if stats and len(stats) != len(block["names"]):
                raise PollFailed(f"stat line of {a['athlete']['displayName']} does not fit the columns")
            players.setdefault(normalize(a["athlete"]["displayName"]), []).append(
                (dict(zip(block["names"], stats)), bool(a.get("didNotPlay"))))
    return {"state": state, "detail": comp["status"]["type"].get("shortDetail") or "", "teams": teams,
            "players": players}


# Legs -------------------------------------------------------------------------------------------------

@dataclass
class Leg:
    n: int
    text: str
    game: int
    kind: str = None           # "moneyline", "player", or None (form not supported)
    team: str = None
    name: str = None
    need: int = None
    stat: str = None
    state: str = "waiting"
    detail: str = ""
    watched: bool = True


def legs_of(slip, unmatched):
    out = []
    for n, leg in enumerate(slip.legs, 1):
        w = Leg(n, leg.text, leg.game)
        prop = PROP.fullmatch(leg.text)
        if leg.team is not None:
            w.kind, w.team = "moneyline", leg.team
        elif prop:
            w.kind, w.name, w.need, w.stat = "player", prop["name"], int(prop["need"]), STATS[prop["stat"]]
        else:
            w.watched, w.state, w.detail = False, "not watched", "form not supported"
        if w.watched and leg.game in unmatched:
            w.watched, w.state, w.detail = False, "not watched", f"game unmatched ({unmatched[leg.game]})"
        out.append(w)
    return out


def stat_value(stats, key):
    """Zero only for an empty stat line (not played yet, or did not play); a non-number raises ValueError."""
    if not stats:
        return 0
    return int(stats[key].split("-")[0]) if key == "3PT" else int(stats[key])


def evaluate(leg, game):
    """(state, detail) for one watched leg against its game as read now."""
    if leg.kind == "moneyline":
        if game["state"] == "pre":
            return "waiting", "game not started"
        mine = [t for t in game["teams"] if team_fits(leg.team, t[0], t[1])]
        if len(mine) != 1:
            return "not watched", "team not found in the game"
        other = next(t for t in game["teams"] if t is not mine[0])
        diff, score = mine[0][2] - other[2], f"{mine[0][2]}-{other[2]}"
        if game["state"] == "post":
            return ("HIT" if diff > 0 else "LOST"), f"final {score}"
        return "live", (f"leads {score}" if diff > 0 else f"trails {score}" if diff < 0 else f"tied {score}")
    if not game["players"]:
        return "waiting", "game not started" if game["state"] == "pre" else "no box score yet"
    found = game["players"].get(normalize(leg.name), [])
    if len(found) != 1:
        return "not watched", "UNMATCHED player"
    stats, dnp = found[0]
    if dnp and game["state"] == "post":
        return "DID NOT PLAY", "check how Kalshi settles it"
    value = stat_value(stats, leg.stat)
    detail = f"{value}/{leg.need} · {stats.get('MIN') or 0} min"
    if value >= leg.need:
        return "HIT", detail
    if game["state"] == "post":
        return "LOST", detail + " · final"
    return "live", detail


def update(legs, games):
    """Move each open leg to its state now; return the legs that just became done or not watched.
    Every leg is read before any is saved, so a poll that fails part-way leaves nothing half-applied."""
    read = [(leg, *evaluate(leg, games[leg.game])) for leg in legs if leg.watched and leg.state not in DONE]
    changes = []
    for leg, state, detail in read:
        if state != leg.state and (state in DONE or state == "not watched"):
            changes.append(leg)
        leg.watched = state != "not watched"
        leg.state, leg.detail = state, detail
    return changes


def ticket(legs):
    """'LOST', 'WON', 'ALL WATCHED HIT' (with legs not watched or not played), or None while open."""
    if any(leg.state == "LOST" for leg in legs):
        return "LOST"
    if any(leg.state in OPEN for leg in legs) or not any(leg.watched for leg in legs):
        return None
    return "WON" if all(leg.state == "HIT" for leg in legs) else "ALL WATCHED HIT"


# Messages ----------------------------------------------------------------------------------------------

def leg_line(leg):
    label = f"{leg.state} · " if leg.state in DONE or not leg.watched else ""
    return f"{ICON[leg.state]} {leg.text}: {label}{leg.detail}"


def unwatched_note(legs):
    k = sum(1 for leg in legs if not leg.watched)
    return [f"{k} leg{'s' if k != 1 else ''} not watched"] if k else []


def board(legs):
    return [leg_line(leg) for leg in legs] + unwatched_note(legs)


RESULT = {"LOST": "TICKET LOST ❌", "WON": "TICKET WON ✅",
          "ALL WATCHED HIT": "ALL WATCHED LEGS HIT — check the legs not watched or not played"}


# The watch ----------------------------------------------------------------------------------------------

def watch(text, every=30, max_hours=6, day=None, texting=True, once=False,
          fetch=espn_get, send=imessage.send, clock=utcnow, sleep=time.sleep, out=print):
    slip = parse(text)
    base = LEAGUES.get(slip.league)
    if base is None:
        raise Unsupported(f"league {slip.league or 'missing'} is not supported")
    started = clock()
    ymd = (day or display_date(started)).replace("-", "")

    def head():
        return f"SLIP WATCH · {display_clock(clock())}"

    def tell(lines):
        msg = "\n".join([head()] + lines)
        out(msg)
        if texting and not once:
            try:
                send(msg)
            except imessage.SendFailed as exc:
                out(f"TEXT FAILED: {exc}")

    try:
        paired, unmatched = pair(slip, fetch(f"{base}/scoreboard?dates={ymd}"))
        legs = legs_of(slip, unmatched)
        games = {g: read_game(fetch(f"{base}/summary?event={eid}")) for g, eid in paired.items()}
        update(legs, games)
    except Exception as exc:                                       # never a quiet failure (rules 7–8)
        tell([f"WATCH FAILED: first poll: {type(exc).__name__}: {exc}"])
        return 1
    for g, why in unmatched.items():
        out(f"UNMATCHED game {' vs '.join(slip.games[g])}: {why}")
    if not any(leg.watched for leg in legs):
        tell(["WATCH FAILED: no leg could be watched"] + board(legs))
        return 1
    tell([f"Watching {sum(leg.watched for leg in legs)} of {len(legs)} legs"] + board(legs))
    if once:
        return 0
    final = {g for g, gm in games.items() if gm["state"] == "post"}

    def settle(finals=()):
        """Send the one ending message and return its exit code once the watch must end, else None."""
        result = ticket(legs)
        if result:
            head, code = RESULT[result], 0
        elif not any(leg.watched for leg in legs):
            head, code = "WATCH FAILED: no leg can be watched", 1
        elif final >= set(paired):
            head, code = "WATCH STOPPED: every watched game final", 0
        else:
            return None
        tell([head, *finals] + board(legs))
        return code

    fails, code = 0, settle()
    while code is None and clock() - started < timedelta(hours=max_hours):
        sleep(every)
        try:
            for g, eid in paired.items():
                if g not in final:
                    games[g] = read_game(fetch(f"{base}/summary?event={eid}"))
            changes = update(legs, games)
        except Exception as exc:                                   # unreadable data is a failed poll, never a zero
            fails += 1
            out(f"POLL FAILED ({fails} in a row): {type(exc).__name__}: {exc}")
            if net.is_permanent(exc) or fails >= FAIL_LIMIT:
                tell([f"WATCH FAILED: {fails} poll{'s' if fails != 1 else ''} in a row failed; stopped "
                      f"({type(exc).__name__}: {exc})"] + board(legs))
                return 1
            continue
        fails = 0
        ended = [g for g, gm in games.items() if gm["state"] == "post" and g not in final]
        final.update(ended)
        finals = [f"FINAL: {' · '.join(f'{a} {s}' for a, _, s in games[g]['teams'])}" for g in ended]
        code = settle(finals)                                      # an ending poll sends only its ending message
        if code is None and (changes or ended):
            tell(finals + [leg_line(leg) for leg in changes]
                 + ["OPEN:"] + [leg_line(leg) for leg in legs if leg.state in OPEN] + unwatched_note(legs))
    if code is not None:
        return code
    tell([f"WATCH STOPPED: time limit ({max_hours:g} h)"] + board(legs))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(prog="python -m sports_edge.watch")
    ap.add_argument("slip")
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--max-hours", type=float, default=6)
    ap.add_argument("--date", type=Date.fromisoformat)
    ap.add_argument("--no-text", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    if args.every < MIN_EVERY:
        print(f"Can't watch: --every must be at least {MIN_EVERY} seconds")
        return 2
    try:
        return watch(open(args.slip, encoding="utf-8").read(), every=args.every, max_hours=args.max_hours,
                     day=args.date.isoformat() if args.date else None, texting=not args.no_text, once=args.once)
    except SlipError as exc:
        print(f"Can't read the slip: {exc}")
        return 2
    except Unsupported as exc:
        print(f"Can't watch: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
