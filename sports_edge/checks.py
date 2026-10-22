"""Kickoff clusters and pre-game check times (docs/specs/slate_alerts_spec.md).

Decides which games get odds pulled on a run, so it is part of the evidence
path: the odds step and the slate both read it.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from . import config
from .store import from_db_ts, to_db_ts
from .timeutil import display_date, pacific_midnight

CLUSTER_SPAN = timedelta(minutes=30)
OFFSETS_MIN = {"free": (30,), "paid": (60, 15)}
STATUSES = ("checked", "no_price", "no_credits", "fee_unknown", "superseded")


@dataclass(frozen=True)
class Game:
    source_event_id: str
    sport_key: str
    commence_utc: object
    home: str
    away: str
    event_ticker: str


@dataclass
class Cluster:
    start_utc: object
    games: list = field(default_factory=list)


def slate_sports():
    """{odds sport key: league label} for the owner's sports on the current plan.
    Keys in _paid_only are off on the free plan."""
    data = config.load_json_config("slate_sports.json")
    off = set(data.get("_paid_only", [])) if config.odds_plan() == "free" else set()
    return {k: v for k, v in data.items() if not k.startswith("_") and k not in off}


def offsets():
    return OFFSETS_MIN[config.odds_plan()]


def slate_games(store, source, start, end):
    """In-scope matched games whose latest listed start is in [start, end), soonest first."""
    scope = slate_sports()
    rows = store.con.execute("""
        SELECT r.source_event_id, r.sport_key, r.commence_utc, r.home, r.away, min(o.event_ticker)
        FROM reference_events r
        JOIN (SELECT source_event_id, max(fetched_utc) AS f FROM reference_events
              WHERE source = ? GROUP BY 1) last
          ON r.source_event_id = last.source_event_id AND r.fetched_utc = last.f
        JOIN odds_matches o ON o.source_event_id = r.source_event_id AND o.source = r.source
        WHERE r.source = ? AND r.commence_utc >= ? AND r.commence_utc < ?
          AND o.event_ticker NOT IN (SELECT event_ticker FROM match_conflicts)
        GROUP BY 1, 2, 3, 4, 5
        ORDER BY 3, 1""", [source, source, to_db_ts(start), to_db_ts(end)]).fetchall()
    return [Game(eid, key, from_db_ts(c), home, away, ev)
            for eid, key, c, home, away, ev in rows if key in scope]


def clusters(games):
    """Per Pacific day: anchor on the day's earliest game; every game starting within
    30 min of the anchor joins it, and the next game anchors the next slot."""
    out = []
    for g in sorted(games, key=lambda g: (g.commence_utc, g.source_event_id)):
        if out and display_date(g.commence_utc) == display_date(out[-1].start_utc) \
                and g.commence_utc - out[-1].start_utc <= CLUSTER_SPAN:
            out[-1].games.append(g)
        else:
            out.append(Cluster(g.commence_utc, [g]))
    return out


def plan(store, source, start, end):
    """Day clusters for games from Pacific midnight of `start` up to `end`. Games that
    have already started are included, so they keep anchoring their slots."""
    return clusters(slate_games(store, source, pacific_midnight(start), end))


def logged_checks(store, source, since):
    """{(source_event_id, offset_min): status} for every check_log row since `since`."""
    return {(e, o): s for e, o, s in store.con.execute(
        "SELECT source_event_id, offset_min, status FROM check_log WHERE source = ? AND ts_utc >= ? "
        "ORDER BY ts_utc", [source, to_db_ts(since)]).fetchall()}


def due_checks(cluster_list, done, now, offset_list):
    """[(cluster, game, offset, superseded offsets)] for checks due at `now`.

    Due: cluster start - offset <= now < game start, with no check_log row for
    (game, offset). If several offsets are due (a missed run), only the
    smallest runs and the others are superseded. Once an offset is logged for a
    game, a larger (earlier) offset never runs for it.
    """
    due = []
    for c in cluster_list:
        for g in c.games:
            if now >= g.commence_utc:
                continue
            logged = [o for o in offset_list if (g.source_event_id, o) in done]
            ready = [o for o in offset_list if c.start_utc - timedelta(minutes=o) <= now
                     and o not in logged and (not logged or o < min(logged))]
            if ready:
                run = min(ready)
                due.append((c, g, run, sorted(o for o in ready if o != run)))
    return due


def check_time(cluster, offset_min):
    return cluster.start_utc - timedelta(minutes=offset_min)


# Read by the slate (docs/specs/slate_alerts_spec.md) -------------------------------------

def latest_starts(store):
    """{source_event_id: latest listed start}."""
    return {e: from_db_ts(c) for e, c in store.con.execute("""
        SELECT r.source_event_id, r.commence_utc FROM reference_events r
        JOIN (SELECT source_event_id, max(fetched_utc) AS f FROM reference_events GROUP BY 1) l
          ON r.source_event_id = l.source_event_id AND r.fetched_utc = l.f""").fetchall()}


def recent_checks(store, source, since):
    """{(cluster start, offset, run_id): [check_log rows as dicts]} logged since `since`, oldest first."""
    groups = {}
    for row in store.con.execute("""
            SELECT run_id, ts_utc, source_event_id, sport_key, cluster_start_utc, offset_min, status
            FROM check_log WHERE source = ? AND ts_utc >= ? ORDER BY ts_utc""",
                                 [source, to_db_ts(since)]).fetchall():
        r = dict(zip(("run_id", "ts_utc", "source_event_id", "sport_key", "cluster_start_utc", "offset_min",
                      "status"), row))
        groups.setdefault((from_db_ts(r["cluster_start_utc"]), r["offset_min"], r["run_id"]), []).append(r)
    return groups


def missed_checks(cluster_list, done, now, offset_list):
    """[(cluster, offset)]: the latest offset whose time has come, for slots with a game still
    ahead and no check logged at that offset for any of its games. done: {(game, offset)}."""
    out = []
    for c in cluster_list:
        ready = [o for o in offset_list if check_time(c, o) <= now]
        if not ready or all(g.commence_utc <= now for g in c.games):
            continue
        if not any((g.source_event_id, min(ready)) in done for g in c.games):
            out.append((c, min(ready)))
    return out


def checked_slots(store, start, end):
    """Distinct (slot, offset) checks with status checked, logged in [start, end)."""
    return store.con.execute(
        "SELECT count(DISTINCT (cluster_start_utc, offset_min)) FROM check_log "
        "WHERE status = 'checked' AND ts_utc >= ? AND ts_utc < ?", [to_db_ts(start), to_db_ts(end)]).fetchone()[0]


def unchecked(store, source, start, end):
    """(games starting in [start, end), [(game, its status or None)] for those never checked,
    the number of slots holding them). A game's status is 'checked' if any check was, else its latest."""
    games = slate_games(store, source, start, end)
    status = {}
    for eid, st in store.con.execute(
            "SELECT source_event_id, status FROM check_log WHERE source = ? AND source_event_id IN "
            "(SELECT unnest(?)) ORDER BY ts_utc", [source, [g.source_event_id for g in games]]).fetchall():
        if status.get(eid) != "checked":
            status[eid] = st
    missed = [(g, status.get(g.source_event_id)) for g in games if status.get(g.source_event_id) != "checked"]
    ids = {g.source_event_id for g, _ in missed}
    slots = sum(1 for c in plan(store, source, start, end) if any(g.source_event_id in ids for g in c.games))
    return games, missed, slots


def check_history(store, source, since):
    """{source_event_id: [(offset, status, time)]} logged since `since`, oldest first."""
    out = {}
    for eid, off, st, ts in store.con.execute(
            "SELECT source_event_id, offset_min, status, ts_utc FROM check_log WHERE source = ? AND ts_utc >= ? "
            "ORDER BY ts_utc", [source, to_db_ts(since)]).fetchall():
        out.setdefault(eid, []).append((off, st, from_db_ts(ts)))
    return out


def sport_key_of(store, market_ticker):
    found = store.con.execute("SELECT sport_key FROM odds_matches WHERE market_ticker = ? LIMIT 1",
                              [market_ticker]).fetchone()
    return found[0] if found else None
