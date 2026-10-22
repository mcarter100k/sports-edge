"""The slate: today's matched games, pre-game check messages, the morning
message and the iCloud sheet (docs/specs/slate_alerts_spec.md).

python -m sports_edge.slate show|alert|morning|wake

A read-only view over what the jobs collect. It writes only replies to the
bet log, its sent-message state, logs/imessage.log, the digest report
(morning) and the sheet. Picks are exactly the ledger rows written at a check.
"""
import json
import math
import os
import statistics
import sys
from datetime import timedelta
from decimal import Decimal

from . import bets, checks, config, digest, fees, health, imessage, ledger
from .grader import latest_replies, taken_bets
from .store import Store, from_db_ts, to_db_ts
from .timeutil import (display, display_clock, display_date, display_day, iso_z, pacific_midnight,
                       parse_utc, pmset_time, utcnow)

SOURCE = "the-odds-api"
MESSAGE_MAX = 300
MORNING_MAX = 700
FRESH = timedelta(minutes=15)          # a check older than this is never shown as picks
STEADY = Decimal("0.02")               # line move under 2 cents either way = steady
POSTPONED = timedelta(minutes=60)
DAILY_CAP = Decimal("30")
WAKE_LEAD = timedelta(minutes=15)
NOT_RUNNING_AFTER = timedelta(hours=2)
STAGES = ((1000, "Confirmed"), (500, "Supported"), (100, "Early signal"))
NEXT_STAGE = {"Early signal": "Supported needs 500", "Supported": "Confirmed needs 1,000"}
PRACTICE_N = 100
DRAWDOWN_UNITS = 20
Z95 = 1.96
NAME_MAX = 16
NAME_MIN = 10                          # names are cut to this only when a message would run over
JARGON = ("clv", "brier", "de-vig", "devig", "overround", " ev ", "edge", "lock", "value")


# Formatting -------------------------------------------------------------------------

def cents(price):
    """0.42 -> '42¢', 0.4235 -> '42.4¢'."""
    v = f"{(Decimal(str(price)) * 100).quantize(Decimal('0.1')):f}".rstrip("0").rstrip(".")
    return f"{v}¢"


def dollars(x):
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def minutes(delta):
    return max(0, int(delta.total_seconds() // 60))


def _short(name, limit=NAME_MAX):
    return name if len(name) <= limit else name[:limit - 1] + "…"


# Stage line -------------------------------------------------------------------------

def _low(xs):
    if len(xs) < 2:
        return None
    return statistics.fmean(xs) - Z95 * statistics.stdev(xs) / math.sqrt(len(xs))


def stage(graded):
    """(stage name, line) from graded first-fire bets, by game (docs/specs/slate_alerts_spec.md)."""
    first = digest.first_fire(graded, "snapshot_utc")
    games = {}
    for r in first:
        games.setdefault(r["event_ticker"], []).append(r)
    n = len(games)
    beat, ret = [], []
    for rs in games.values():
        have = [float(r["clv"]) for r in rs if r.get("clv") not in (None, "")]
        if have:
            beat.append(statistics.fmean(have))
        ret.append(sum(float(r["pnl_per_contract"]) for r in rs)
                   / sum(float(r["price_paid"]) + float(r["fee_per_contract"]) for r in rs))
    cum = peak = 0.0
    for r in first:
        cum += float(r["pnl_per_contract"]) / (float(r["price_paid"]) + float(r["fee_per_contract"]))
        peak = max(peak, cum)
    if n >= PRACTICE_N and beat and statistics.fmean(beat) <= 0:
        return "Practice", f"Practice (demoted: not beating the final price) · {n} games"
    if peak - cum >= DRAWDOWN_UNITS:
        return "Practice", f"Practice (demoted: {DRAWDOWN_UNITS}-bet losing stretch) · {n} games"
    beat_low = _low(beat)
    for need, name in STAGES:
        if n < need or beat_low is None or beat_low <= 0:
            continue
        if name == "Early signal" or (name == "Supported" and statistics.fmean(ret) > 0) or \
                (name == "Confirmed" and (_low(ret) or 0) > 0):
            return name, " · ".join([name, f"{n} games"] + ([NEXT_STAGE[name]] if name in NEXT_STAGE else []))
    return "Practice", f"Practice · {n} of {PRACTICE_N} games"


# Picks ------------------------------------------------------------------------------

def side_fair(row):
    fair = Decimal(row["fair_prob"])
    return fair if row["side"] == "YES" else Decimal(1) - fair


def pay_up_to(win, multiplier, precision, stake, threshold):
    """Highest whole cent where the side's fair chance - price - taker fee >= threshold, or None."""
    best = None
    for c in range(1, 100):
        p = Decimal(c) / 100
        n = int(Decimal(stake) // p)
        if n >= 1 and win - p - fees.fee_per_contract(p, n, multiplier, "taker", precision) >= threshold:
            best = c
    return best


def wording(row, teams, limit=NAME_MAX):
    """(who the pick backs, as words; the name 'toward' refers to)."""
    title, yes = row["outcome"], row["side"] == "YES"
    names = [_short(t, limit) for t in teams["titles"]]
    me = _short(title, limit)
    opp = next((t for t in names if t != me), "?")
    if title == "Tie":
        a, b = (names + ["?", "?"])[:2]
        return (f"Draw: {a} vs {b} (Yes on Tie)", "a draw") if yes else \
            (f"No draw: {a} vs {b} (No on Tie)", "no draw")
    if yes:
        return f"{me} over {opp} (Yes on {me})", me
    if teams["tie"]:
        return f"{opp} or a draw vs {me} (No on {me})", f"{opp} or a draw"
    return f"{opp} over {me} (No on {me})", opp


def line_label(row, backed):
    move = row.get("kalshi_move_1h")
    if move in (None, ""):
        return "no earlier price"
    move = Decimal(move) * (1 if row["side"] == "YES" else -1)
    if abs(move) < STEADY:
        return "line steady"
    return f"moved toward {backed}, check news" if move > 0 else "moved against, skip"


def pick_text(row, number, again, teams, league, now, several=False, compact=False):
    """One pick in the owner's format. compact: the fallback when a message would run over
    300 characters (10-character names, no league, no 'again', 'toward this pick')."""
    who, backed = wording(row, teams, NAME_MIN if compact else NAME_MAX)
    if compact:
        again, backed = False, "this pick"
    pay = pay_up_to(side_fair(row), Decimal(row["fee_multiplier"]), Decimal(row["fee_precision"]),
                    Decimal(row["stake_dollars"]), Decimal(row["threshold"]))
    paid = cents(row["price_paid"]) + (" avg" if ";" in (row.get("fill_detail") or "") else "")
    age = minutes(now - parse_utc(row["snapshot_utc"]))
    head = (f"#{number}{' again' if again else ''} {who} · {'' if compact else league + ' · '}"
            f"{display_clock(parse_utc(row['commence_utc']))} · Pay up to {pay}¢ · now {paid} · "
            f"fair {cents(side_fair(row))} · {line_label(row, backed)} · price {age} min old")
    tag = f"{number} " if several else ""
    reply = f'Reply "took {tag}{cents(row["price_paid"])[:-1]}" or "skip{" " + str(number) if several else ""}"'
    return f"{head}\n{reply}"


def combo_line(picks, table):
    """picks: [(number, row)] from one check. None unless 2+ different games."""
    legs, seen = [], set()
    for n, r in sorted(picks, key=lambda p: p[0]):
        if r["event_ticker"] not in seen:
            seen.add(r["event_ticker"])
            legs.append((n, r))
    if len(legs) < 2:
        return None
    label = "Combo " + "+".join(f"#{n}" for n, _ in legs)
    try:
        mult, precision = fees.combo_taker_multiplier(table), fees.table_precision(table)
    except fees.FeeScheduleUnknown:
        return f"{label}: fee unconfirmed — no price."
    fair = math.prod(side_fair(r) for _, r in legs)
    best = None
    for c in range(1, 100):
        p = Decimal(c) / 100
        if p + fees.fee_per_contract(p, int(bets.STAKE // p), mult, "taker", precision) <= fair:
            best = c
    if best is None:
        return f"{label}: no price works — let any quote expire."
    return f"{label}: accept a quote at {best}¢ or less, else let it expire."


# Context ----------------------------------------------------------------------------

class Context:
    """Everything one invocation reads, read once."""

    def __init__(self, store, now, replies):
        self.store, self.now, self.replies = store, now, replies
        self.leagues = checks.slate_sports()
        self.ledger = ledger.read_rows(config.shadow_ledger_path())
        self.ledger_by_id = {r["row_id"]: r for r in self.ledger}
        self.graded = ledger.read_rows(config.shadow_grades_path())
        self.stage_name, self.stage_line = stage(self.graded)
        self.fee_state = digest.fee_table_state()
        self.table = fees.load_table() if self.fee_state == "ok" else None
        snap = health.read_state().get("snapshot", {})
        self.snapshot_failing = bool(snap.get("consecutive_failures"))
        self.taken = taken_bets(store, self.ledger, ledger.read_rows(config.bet_log_path()))
        today = display_date(now)
        settled_today = sum((b["dollars"] for b in self.taken
                             if b["pick_date"] == today and b["dollars"] is not None), Decimal(0))
        self.day_result = settled_today
        self.cap_reached = bool(replies.get("readable")) and settled_today <= -DAILY_CAP
        self._titles = {}

    def footer(self):
        lines = [self.stage_line]
        if not self.replies.get("readable"):
            lines.append("Bet log unreadable: cap not tracked")
        return "\n".join(lines)

    def teams(self, event_ticker):
        if event_ticker not in self._titles:
            titles = [t for (t,) in self.store.con.execute(
                "SELECT yes_sub_title FROM markets WHERE event_ticker = ? ORDER BY ticker",
                [event_ticker]).fetchall()]
            self._titles[event_ticker] = {"titles": [t for t in titles if t != "Tie"], "tie": "Tie" in titles}
        return self._titles[event_ticker]

    def league(self, sport_key):
        return self.leagues.get(sport_key, sport_key)

    def last_snapshot(self):
        row = self.store.con.execute(
            "SELECT status, ts_utc FROM job_runs WHERE job = 'snapshot' AND phase = 'finish' "
            "ORDER BY ts_utc DESC LIMIT 1").fetchone()
        return (row[0], from_db_ts(row[1])) if row else (None, None)

    def run_finish(self, run_id):
        row = self.store.con.execute(
            "SELECT status, ts_utc FROM job_runs WHERE run_id = ? AND phase = 'finish'", [run_id]).fetchone()
        return (row[0], from_db_ts(row[1])) if row else (None, None)

    def latest_starts(self):
        return {e: from_db_ts(c) for e, c in self.store.con.execute("""
            SELECT r.source_event_id, r.commence_utc FROM reference_events r
            JOIN (SELECT source_event_id, max(fetched_utc) AS f FROM reference_events GROUP BY 1) l
              ON r.source_event_id = l.source_event_id AND r.fetched_utc = l.f""").fetchall()}

    def picks_of_run(self, run_id, event_ids):
        """[(number, again, row)] for ledger rows written by one run, games not started."""
        rows = [r for r in self.ledger if r["run_id"] == run_id and r["source_event_id"] in event_ids
                and parse_utc(r["commence_utc"]) > self.now]
        first_ids = bets.first_fire_ids(self.ledger)
        numbers = {}
        for r in rows:
            day = display_date(parse_utc(self.ledger_by_id[first_ids[bets.bet_key(r)]]["logged_utc"]))
            numbers.setdefault(day, bets.day_picks(self.ledger, day))
        out = []
        for r in rows:
            first = self.ledger_by_id[first_ids[bets.bet_key(r)]]
            n, _ = numbers[display_date(parse_utc(first["logged_utc"]))][bets.bet_key(r)]
            out.append((n, first["row_id"] != r["row_id"], r))
        return sorted(out, key=lambda p: (p[0], p[2]["side"]))


def slot(ctx, start, sport_keys):
    counts = {}
    for k in sport_keys:
        counts[ctx.league(k)] = counts.get(ctx.league(k), 0) + 1
    return f"{display_clock(start)} · " + ", ".join(f"{lg} {n}" for lg, n in sorted(counts.items()))


# Alerts -----------------------------------------------------------------------------

def cluster_messages(ctx, cstart, offset, rows, sent):
    """[(key, [texts])] for one run's check_log rows of one (cluster, offset)."""
    now = ctx.now
    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    live = [r for r in rows if r["status"] != "superseded"]
    where = slot(ctx, cstart, [r["sport_key"] for r in live])
    texts, extra = [], []
    run_id = rows[-1]["run_id"]
    run_status, run_end = ctx.run_finish(run_id)
    checked = {r["source_event_id"] for r in by.get("checked", [])}
    if checked:
        if run_status != "ok" or ctx.snapshot_failing:
            texts.append(f"Don't bet now: prices not checked (system problem). {where}")
        elif now - run_end > FRESH:
            texts.append(f"Don't bet now: prices not checked (prices are {minutes(now - run_end)} min old). "
                         f"{where}")
        else:
            picks = ctx.picks_of_run(run_id, checked)
            age = minutes(now - min((parse_utc(p[2]["snapshot_utc"]) for p in picks), default=run_end))
            if picks and ctx.cap_reached:
                texts.append(f"Daily cap reached — no more bets today. ({len(picks)} price gaps logged "
                             f"for the record.)")
            elif picks:
                for n, again, r in picks:
                    league = ctx.league(next(x["sport_key"] for x in rows
                                             if x["source_event_id"] == r["source_event_id"]))
                    args = (r, n, again, ctx.teams(r["event_ticker"]), league, now, len(picks) > 1)
                    text = pick_text(*args)
                    if len(f"{text}\n{ctx.footer()}") > MESSAGE_MAX:
                        text = pick_text(*args, compact=True)
                    texts.append(text)
                combo = combo_line([(n, r) for n, _, r in picks if isinstance(n, int)], ctx.table)
                if combo:
                    texts.append(combo)
            else:
                texts.append(f"{where}: checked, no price gaps · prices {age} min old")
    if by.get("no_price"):
        texts.append(f"{slot(ctx, cstart, [r['sport_key'] for r in by['no_price']])}: "
                     f"no sharp price available, not checked.")
    if by.get("fee_unknown"):
        if ctx.fee_state != "ok":
            extra.append((f"{display_date(now)}|nofeetable",
                          [f"Don't bet today: prices not checked (no fee table)."]))
        else:
            leagues = sorted({ctx.league(r["sport_key"]) for r in by["fee_unknown"]})
            texts.append(f"Don't bet now: prices not checked (fee not confirmed for {', '.join(leagues)}). "
                         f"{slot(ctx, cstart, [r['sport_key'] for r in by['fee_unknown']])}")
    if by.get("no_credits"):
        later = checks.clusters(checks.slate_games(ctx.store, SOURCE, cstart, day_window(now)[1]))
        extra.append((f"{display_date(now)}|nocredits",
                      [f"Out of odds credits today: {len(later)} later time slots not checked — don't bet them."]))
    return [(f"{iso_z(cstart)}|{offset}|{run_id}", texts)] + [(k, t) for k, t in extra if k not in sent]


def alert_messages(ctx, sent):
    """[(key, [texts])] not yet sent: checks that ran, and due checks a failing system missed."""
    now, store = ctx.now, ctx.store
    rows = [dict(zip(("run_id", "ts_utc", "source_event_id", "sport_key", "cluster_start_utc", "offset_min",
                      "status"), r)) for r in store.con.execute("""
        SELECT run_id, ts_utc, source_event_id, sport_key, cluster_start_utc, offset_min, status
        FROM check_log WHERE source = ? AND ts_utc >= ? ORDER BY ts_utc""",
                                                                 [SOURCE, to_db_ts(now - timedelta(hours=3))]).fetchall()]
    groups = {}
    for r in rows:
        groups.setdefault((from_db_ts(r["cluster_start_utc"]), r["offset_min"], r["run_id"]), []).append(r)
    out, seen_keys = [], set(sent)
    for (cstart, offset, run_id), grp in sorted(groups.items()):
        key = f"{iso_z(cstart)}|{offset}|{run_id}"
        if key in seen_keys:
            continue
        if cstart <= now:
            out.append((key, []))          # the slot has started: never send old picks
            continue
        for k, texts in cluster_messages(ctx, cstart, offset, grp, seen_keys):
            out.append((k, texts))
            seen_keys.add(k)
    status, _ = ctx.last_snapshot()
    if ctx.snapshot_failing or status == "failed":
        done = {(r["source_event_id"], r["offset_min"]) for r in rows}
        for c in checks.clusters(checks.slate_games(store, SOURCE, now, now + timedelta(hours=24))):
            ready = [o for o in checks.offsets() if checks.check_time(c, o) <= now]
            if not ready:
                continue
            key = f"{iso_z(c.start_utc)}|{min(ready)}|missed"
            if key in seen_keys or any((g.source_event_id, min(ready)) in done for g in c.games):
                continue
            out.append((key, [f"Don't bet now: prices not checked (system problem). "
                              f"{slot(ctx, c.start_utc, [g.sport_key for g in c.games])}"]))
            seen_keys.add(key)
    return out


# Morning ----------------------------------------------------------------------------

def day_window(now, days_back=0):
    start = pacific_midnight(now)
    for _ in range(days_back):
        start = pacific_midnight(start - timedelta(hours=1))
    return start, pacific_midnight(start + timedelta(hours=30))


def pick_results(ctx, picks):
    """Paper results at $10 per pick: (won, lost, void, open, dollars, staked)."""
    tickers = sorted({r["market_ticker"] for r in picks})
    results = dict(ctx.store.con.execute(
        "SELECT ticker, result FROM settlements WHERE ticker IN (SELECT unnest(?))", [tickers]).fetchall()) \
        if tickers else {}
    won = lost = void = open_ = 0
    money = staked = Decimal(0)
    for r in picks:
        res = results.get(r["market_ticker"])
        n = int(r["contracts_assumed"])
        if res is None:
            open_ += 1
            continue
        if res not in ("yes", "no"):
            void += 1
            continue
        win = (r["side"] == "YES") == (res == "yes")
        won, lost = won + win, lost + (not win)
        cost = (Decimal(r["price_paid"]) + Decimal(r["fee_per_contract"])) * n
        money += (Decimal(n) if win else Decimal(0)) - cost
        staked += cost
    return won, lost, void, open_, money, staked


def problems(ctx):
    out = []
    state = health.read_state()
    for job in ("snapshot", "grader"):
        if state.get(job, {}).get("consecutive_failures"):
            out.append(f"{job} failing")
    status, last = ctx.last_snapshot()
    if last is None or ctx.now - last > NOT_RUNNING_AFTER:
        out.append("prices not updating" if last else "no price run yet")
    if ctx.fee_state != "ok":
        out.append(f"fee table {ctx.fee_state}")
    if not config.odds_api_key():
        out.append("odds not set up")
    if not ctx.replies.get("readable"):
        out.append("bet log unreadable")
    return out


def today_line(ctx, now):
    start, end = day_window(now)
    cl = checks.clusters(checks.slate_games(ctx.store, SOURCE, start, end))
    if not cl:
        why = " (odds not set up)" if not config.odds_api_key() else ""
        return "Today: no matched games" + why
    n = sum(len(c.games) for c in cl)
    slots = [slot(ctx, c.start_utc, [g.sport_key for g in c.games]).replace(" · ", " ", 1) for c in cl]
    shown = " · ".join(slots[:6]) + (f" · +{len(slots) - 6} more in the sheet" if len(slots) > 6 else "")
    return f"Today: {n} games, {len(cl)} time slots (PT): {shown}"


def unable_reason(ctx):
    """Why no check could have run (rule 8: name the cause, never print a bare zero), or None."""
    if not config.odds_api_key():
        return "odds not set up"
    if ctx.fee_state != "ok":
        return "no fee table" if ctx.fee_state == "missing" else "fee table invalid"
    return None


def yesterday_lines(ctx, now):
    y0, y1 = day_window(now, 1)
    day = display_date(y0)
    checked = ctx.store.con.execute(
        "SELECT count(DISTINCT (cluster_start_utc, offset_min)) FROM check_log "
        "WHERE status = 'checked' AND ts_utc >= ? AND ts_utc < ?", [to_db_ts(y0), to_db_ts(y1)]).fetchone()[0]
    picks = [r for _, r in bets.day_picks(ctx.ledger, day).values()]
    games = len({r["event_ticker"] for r in picks})
    won, lost, void, open_, money, staked = pick_results(ctx, picks)
    cannot = unable_reason(ctx)
    if not checked and not picks and cannot:
        line = f"Yesterday: nothing checked ({cannot})"
    else:
        line = f"Yesterday: {checked} checks, {len(picks)} price gaps in {games} games"
    if picks:
        line += f" · results: {won} won, {lost} lost, {open_} open" + (f", {void} void" if void else "")
        if won + lost:
            line += f" · on paper {dollars(money)} on ${staked:.0f}"
    lines = [line]
    if not ctx.replies.get("readable"):
        lines.append("Your bets: bet log unreadable")
    else:
        mine = [b for b in ctx.taken if b["pick_date"] == day]
        skipped = sum(1 for r in latest_replies(ledger.read_rows(config.bet_log_path())).values()
                      if r["action"] == "skip" and r["pick_date"] == day)
        settled = [b["dollars"] for b in mine if b["dollars"] is not None]
        lines.append(f"You took {len(mine)}" + (f": {dollars(sum(settled, Decimal(0)))}" if settled else "")
                     + (f" ({len(mine) - len(settled)} open)" if len(settled) < len(mine) else "")
                     + f" · skipped {skipped}")
    if config.odds_plan() == "free":
        lines.append(unchecked_line(ctx, y0, y1))
    return lines


def unchecked_line(ctx, y0, y1):
    games = checks.slate_games(ctx.store, SOURCE, y0, y1)
    if not games:
        why = " (odds not set up)" if not config.odds_api_key() else ""
        return f"Unchecked yesterday: no matched games yesterday{why}"
    status = {}
    for eid, st in ctx.store.con.execute(
            "SELECT source_event_id, status FROM check_log WHERE source = ? AND source_event_id IN "
            "(SELECT unnest(?)) ORDER BY ts_utc", [SOURCE, [g.source_event_id for g in games]]).fetchall():
        if status.get(eid) != "checked":
            status[eid] = st
    missed = [g for g in games if status.get(g.source_event_id) != "checked"]
    if not missed:
        return f"Unchecked yesterday: none (all {len(games)} games checked)"
    why = {}
    for g in missed:
        label = {"no_credits": "out of credits", "fee_unknown": "fee not confirmed", "no_price": "no sharp price",
                 "superseded": "not running", None: "not running"}[status.get(g.source_event_id)]
        why[label] = why.get(label, 0) + 1
    slots = len(checks.clusters(missed))
    return (f"Unchecked yesterday: {len(missed)} games in {slots} time slots ("
            + ", ".join(f"{k} {v}" for k, v in sorted(why.items())) + ")")


def morning_text(ctx, now):
    trouble = problems(ctx)
    system = "System OK" if not trouble else "System: " + "; ".join(trouble)
    lines = [f"Kalshi slate · {display_day(now)}", ctx.stage_line, today_line(ctx, now)]
    lines += yesterday_lines(ctx, now)
    lines.append(f"{system} · full sheet: iCloud Drive/sports-edge/sheet.txt")
    text = "\n".join(lines)
    if len(text) > MORNING_MAX:
        raise ValueError(f"morning message is {len(text)} characters, over {MORNING_MAX}")
    return text


# Sheet ------------------------------------------------------------------------------

def sheet_lines(ctx, now, digest_lines):
    store = ctx.store
    status, last = ctx.last_snapshot()
    newest = from_db_ts(store.con.execute("SELECT max(ts_utc) FROM price_snapshots").fetchone()[0])
    age = f"{display(newest)} ({minutes(now - newest)} min old)" if newest else "none yet"
    lines = [f"Slate sheet · built {display(now)}", ctx.stage_line,
             f"Newest price: {age} · last price run {display(last)} ({status or 'none'})",
             "Bet log: " + ("read" if ctx.replies.get("readable") else f"unreadable ({ctx.replies.get('error')})")
             + (" · daily cap reached" if ctx.cap_reached else f" · today's settled bets {dollars(ctx.day_result)}"
                if ctx.replies.get("readable") else " · daily cap not tracked"),
             "", "TODAY (Pacific time)"]
    start, end = day_window(now)
    cl = checks.clusters(checks.slate_games(store, SOURCE, start, end))
    logged = {}
    for eid, off, st, ts in store.con.execute(
            "SELECT source_event_id, offset_min, status, ts_utc FROM check_log WHERE source = ? AND ts_utc >= ? "
            "ORDER BY ts_utc", [SOURCE, to_db_ts(start - timedelta(hours=2))]).fetchall():
        logged.setdefault(eid, []).append(f"{off} min before: {st} at {display_clock(from_db_ts(ts))}")
    if not cl:
        lines.append("  no matched games" + ("" if config.odds_api_key() else " (odds not set up)"))
    for c in cl:
        lines.append(f"  {display_clock(c.start_utc)} slot · checks at "
                     + ", ".join(display_clock(checks.check_time(c, o)) for o in checks.offsets()))
        for g in c.games:
            lines.append(f"    {g.away} at {g.home} · {ctx.league(g.sport_key)} · {display_clock(g.commence_utc)}"
                         f" · {'; '.join(logged.get(g.source_event_id, [])) or 'not checked yet'}")
    starts = ctx.latest_starts()
    today = display_date(now)
    picks = bets.day_picks(ctx.ledger, today)
    newest_row = {}
    for r in sorted(ctx.ledger, key=lambda r: r["logged_utc"]):
        newest_row[bets.bet_key(r)] = r
    open_picks, postponed = [], []
    for key, (n, first) in sorted(picks.items(), key=lambda kv: kv[1][0]):
        r = newest_row[key]
        latest = starts.get(r["source_event_id"])
        if latest and latest - parse_utc(r["commence_utc"]) >= POSTPONED:
            postponed.append(f"  #{n} postponed — void (now listed {display(latest)})")
        elif parse_utc(r["commence_utc"]) > now:
            open_picks.append((n, r))
    lines += ["", "OPEN PICKS (games not started; prices as of each pick's latest check)"]
    lines += [f"  {pick_text(r, n, False, ctx.teams(r['event_ticker']), ctx.league(sport_of(store, r)), now)}"
              .replace("\n", " · ") for n, r in open_picks] or ["  none"]
    combo = combo_line(open_picks, ctx.table)
    if combo:
        lines.append(f"  {combo}")
    if postponed:
        lines += ["", "POSTPONED"] + postponed
    lines += ["", "YOUR REPLIES TODAY"]
    replies = [r for r in ledger.read_rows(config.bet_log_path()) if r["pick_date"] == today]
    lines += [f"  #{r['pick_no'] or '?'} {r['action']}" + (f" at {cents(r['price'])}" if r["price"] else "")
              + f" ({r['raw_text']!r})" for r in replies] or ["  none"]
    lines += ["", "YESTERDAY"] + [f"  {ln}" for ln in yesterday_lines(ctx, now)]
    lines += ["", "DAILY REPORT"] + [f"  {ln}" for ln in digest_lines]
    lines += ["", "HOW TO READ THIS",
              "  A pick is a price gap of at least 3¢ after Kalshi's fee, against the sharp book's fair price,",
              "  logged by the system. Pay up to = the highest price that still leaves 3¢. Practice stake $10.",
              "  Line = Kalshi's price for the pick over the last hour (steady = under 2¢ either way).",
              "  Nothing here places a bet."]
    return lines


def sport_of(store, row):
    found = store.con.execute("SELECT sport_key FROM odds_matches WHERE market_ticker = ? LIMIT 1",
                              [row["market_ticker"]]).fetchone()
    return found[0] if found else row["sport"]


def _replace_atomically(path, text):
    """Write a new temp file (exclusive create, never an overwrite mode), then rename it over path.
    Only for views and operational state, never for evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    with open(tmp, "x", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def write_sheet(lines):
    return _replace_atomically(config.sheet_dir() / "sheet.txt", "\n".join(lines) + "\n")


def digest_section(now):
    try:
        line, report, _ = digest.build_digest(now)
        return line, report, report
    except Exception as exc:  # noqa: BLE001 - shown in the sheet, never hidden
        return None, None, [f"daily report unavailable: {type(exc).__name__}: {exc}"]


# Sent state -------------------------------------------------------------------------

def _sent_path():
    return config.state_dir() / "slate_sent.json"


def load_sent():
    path = _sent_path()
    return json.loads(path.read_text()) if path.exists() else {}


def save_sent(sent, now):
    keep = {k: v for k, v in sent.items() if now - parse_utc(v) <= timedelta(days=3)}
    _replace_atomically(_sent_path(), json.dumps(keep, indent=1, sort_keys=True))


# Commands ---------------------------------------------------------------------------

def run_alert(now=None, send=imessage.send, out=print):
    now = now or utcnow()
    replies = bets.ingest_replies(now)
    _, _, digest_lines = digest_section(now)
    sent = load_sent()
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now, replies)
        msgs = alert_messages(ctx, sent)
        sheet = sheet_lines(ctx, now, digest_lines)
    count = 0
    for key, texts in msgs:
        for text in texts:
            full = f"{text}\n{ctx.footer()}"
            if len(full) > MESSAGE_MAX:
                raise ValueError(f"alert is {len(full)} characters, over {MESSAGE_MAX}: {full}")
            send(full)
            count += 1
        sent[key] = iso_z(now)
        save_sent(sent, now)
    path = write_sheet(sheet)
    out(f"SLATE ALERT OK messages={count} groups={len(msgs)} replies_logged={replies['logged']} "
        f"bet_log={'read' if replies['readable'] else 'unreadable'} sheet={path}")
    return count


def run_morning(now=None, send=imessage.send, out=print):
    now = now or utcnow()
    replies = bets.ingest_replies(now)
    _, report, digest_lines = digest_section(now)
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now, replies)
        text = f"{morning_text(ctx, now)}"
        sheet = sheet_lines(ctx, now, digest_lines)
    report_path = digest.write_report(report, now) if report else None
    send(text)
    path = write_sheet(sheet)
    out(text)
    out(f"SLATE MORNING OK report={report_path} sheet={path}")
    return text


def run_show(now=None, out=print):
    now = now or utcnow()
    _, _, digest_lines = digest_section(now)
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now, {"readable": False, "error": "not read by show"})
        for line in sheet_lines(ctx, now, digest_lines):
            out(line)


def wake_times(now=None):
    """(wake, first, last) over check times still ahead in the next 24 h, or None."""
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        games = checks.slate_games(store, SOURCE, now, now + timedelta(hours=24))
    times = [checks.check_time(c, o) for c in checks.clusters(games) for o in checks.offsets()]
    ahead = sorted(t for t in times if t > now)
    if not ahead:
        return None
    return ahead[0] - WAKE_LEAD, ahead[0], ahead[-1]


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "alert":
        run_alert()
    elif cmd == "morning":
        run_morning()
    elif cmd == "show":
        run_show()
    elif cmd == "wake":
        found = wake_times()
        print("wake=none (no check ahead in 24 h)" if found is None else
              f"wake={pmset_time(found[0])} first={display(found[1])} last={display(found[2])}")
    else:
        print("usage: python -m sports_edge.slate show|alert|morning|wake", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001
        print(f"SLATE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
