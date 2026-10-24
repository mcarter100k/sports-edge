"""The slate: today's matched games, pre-game check messages, the morning
message and the iCloud sheet (docs/specs/slate_alerts_spec.md).

python -m sports_edge.slate show|board|alert|morning

Formats and sends what shadow, checks, bets and grader decide. It writes only
replies to the bet log (through bets), its sent-message state,
logs/imessage.log and the sheet. Picks are exactly the ledger rows written at a
check; a two-team game's "Yes on A" and "No on B" are one result, shown once
(the cheaper), and both stay in the ledger.
"""
import json
import math
import os
import sys
from datetime import timedelta
from decimal import Decimal

from . import bets, board, checks, config, fees, grader, health, imessage, ledger
from .shadow import pay_up_to
from .store import Store, to_db_ts
from .timeutil import display, display_clock, display_date, display_day, iso_z, pacific_day, parse_utc, utcnow

SOURCE = "the-odds-api"
MESSAGE_MAX = 300
MORNING_MAX = 700
FRESH = timedelta(minutes=15)          # a check older than this is never shown as picks
BOARD_MAX = 10
STEADY = Decimal("0.02")               # a move under 2 cents either way = steady
NOT_RUNNING_AFTER = timedelta(hours=2)
NAME_MAX = 16
NAME_MIN = 10                          # names are cut to this only when a message would run over
UNCHECKED_WORDS = {"no_credits": "out of credits", "fee_unknown": "fee not confirmed",
                   "no_price": "no sharp price", "superseded": "not running", None: "not running"}


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


def side_fair(row):
    fair = Decimal(row["fair_prob"])
    return fair if row["side"] == "YES" else Decimal(1) - fair


def combo_line(picks, table, target=None):
    """picks: [(number, row)] from one check (or the board's current gaps). None unless 2+ different games.
    Legs are exactly these picks, never padded to reach the owner's payout target."""
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
    p = Decimal(best) / 100
    cost = p + fees.fee_per_contract(p, int(bets.STAKE // p), mult, "taker", precision)
    pays = (Decimal(10) / cost).to_integral_value(rounding="ROUND_FLOOR") / 10
    target = config.combo_target_payout() if target is None else target
    goal = f"Your {target.normalize():f}× target: " + ("met." if pays >= target else "below.")
    return f"{label}: accept a quote at {best}¢ or less (pays {pays}× or more), else let it expire. {goal}"


def wording(row, teams, limit=NAME_MAX):
    """(who the pick backs, as words; the name 'toward' refers to)."""
    title, yes = row["outcome"], row["side"] == "YES"

    def cut(names):   # shorten, unless that would make two different names read the same
        short = [_short(t, limit) for t in names]
        return short if len(set(short)) == len(set(names)) else list(names)
    if title == "Tie":
        a, b = (cut(teams["titles"]) + ["?", "?"])[:2]
        return (f"Draw: {a} vs {b} (Yes on Tie)", "a draw") if yes else \
            (f"No draw: {a} vs {b} (No on Tie)", "no draw")
    me, opp = cut([title, next((t for t in teams["titles"] if t != title), "?")])
    if yes:
        return f"{me} over {opp} (Yes on {me})", me
    if teams["tie"]:
        return f"{opp} or a draw vs {me} (No on {me})", f"{opp} or a draw"
    return f"{opp} over {me} (No on {me})", opp


def line_label(row, backed, earlier=None):
    """earlier: the side's fair chance at the previous sharp-book read of this game, or None.

    Two reads (the paid plan): the sportsbook line's move, with its advice. One read: only
    the Kalshi price over the last hour, which says nothing about news.
    """
    if earlier is not None:
        move = side_fair(row) - earlier
        if abs(move) < STEADY:
            return "line steady"
        return f"line moved toward {backed}, check news" if move > 0 else "line moved against, skip"
    move = row.get("kalshi_move_1h")
    if move in (None, ""):
        return "no earlier Kalshi price"
    move = Decimal(move) * (1 if row["side"] == "YES" else -1)
    if abs(move) < STEADY:
        return "Kalshi price steady"
    return "Kalshi price rising" if move > 0 else "Kalshi price falling"


def pick_text(row, number, again, teams, league, now, several=False, compact=False, earlier=None):
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
            f"fair {cents(side_fair(row))} · {line_label(row, backed, earlier)} · price {age} min old")
    tag = f"{number} " if several else ""
    reply = f'Reply "took {tag}{cents(row["price_paid"])[:-1]}" or "skip{" " + str(number) if several else ""}"'
    return f"{head}\n{reply}"


def slot(ctx, start, sport_keys):
    counts = {}
    for k in sport_keys:
        counts[ctx.league(k)] = counts.get(ctx.league(k), 0) + 1
    return f"{display_clock(start)} · " + ", ".join(f"{lg} {n}" for lg, n in sorted(counts.items()))


def unclear_text(text):
    return f'Reply "{text[:40]}" not matched to a pick: send it again with the pick number, e.g. "took 2 42".'


# Context ----------------------------------------------------------------------------

class Context:
    """Everything one invocation reads, read once."""

    def __init__(self, store, now, ingest=True):
        """ingest: read new reply lines into the bet log first (alert, morning); show never does."""
        self.store, self.now, self._titles = store, now, {}
        self.leagues = checks.slate_sports()
        self.ledger = ledger.read_rows(config.shadow_ledger_path())
        self.replies = bets.ingest_replies(now, self.ledger, self.result_of) if ingest else \
            {"readable": False, "error": "not read by show", "logged": 0, "unclear": 0, "unclear_lines": []}
        self.stage_line = grader.stage(ledger.read_rows(config.shadow_grades_path()),
                                       closes_possible=config.odds_plan() == "paid")[1]
        self.fee_state = fees.table_state()
        self.table = fees.load_table() if self.fee_state == "ok" else None
        self.snapshot_failing = bool(health.read_state().get("snapshot", {}).get("consecutive_failures"))
        self.taken = grader.taken_bets(store, self.ledger, ledger.read_rows(config.bet_log_path()))
        self.at_risk = bets.money_at_risk(self.taken, display_date(now))
        self.cap_reached = bool(self.replies["readable"]) and self.at_risk >= bets.DAILY_CAP

    def result_of(self, row):
        return bets.result_key(row, self.teams(row["event_ticker"]))

    def earlier_fair(self, row):
        """The side's fair chance at the sharp-book read before this row's, or None (read once)."""
        found = self.store.con.execute("""
            SELECT fair_prob FROM reference_odds
            WHERE source = ? AND source_event_id = ? AND outcome = ? AND fetched_utc < ?
            ORDER BY fetched_utc DESC LIMIT 1""", [row["odds_source"], row["source_event_id"], row["ref_outcome"],
                                                   to_db_ts(parse_utc(row["odds_fetched_utc"]))]).fetchone()
        if found is None:
            return None
        fair = Decimal(str(found[0])).quantize(Decimal("0.000001"))
        return fair if row["side"] == "YES" else Decimal(1) - fair

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


# Alerts -----------------------------------------------------------------------------

def cluster_messages(ctx, cstart, offset, rows, sent):
    """[(key, [texts])] for one run's check_log rows of one (cluster, offset)."""
    now = ctx.now
    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    keys_of = lambda status, keep=lambda r: True: [r["sport_key"] for r in by.get(status, []) if keep(r)]
    where = slot(ctx, cstart, [r["sport_key"] for r in rows if r["status"] != "superseded"])
    texts, extra = [], []
    run_id = rows[-1]["run_id"]
    run_status, run_end, summary = ctx.store.run_finish(run_id)
    checked = {r["source_event_id"] for r in by.get("checked", [])}
    if checked:
        if run_status != "ok" or ctx.snapshot_failing:
            texts.append(f"Don't bet now: prices not checked (system problem). {where}")
        elif now - run_end > FRESH:
            texts.append(f"Don't bet now: prices not checked (prices are {minutes(now - run_end)} min old). "
                         f"{where}")
        else:
            picks = bets.picks_in_run(ctx.ledger, run_id, ctx.result_of, checked, now)
            compared = set(summary.get("compared_events", [])) & checked
            age = minutes(now - min((parse_utc(p[2]["snapshot_utc"]) for p in picks), default=run_end))
            several = len(bets.shown_in_run(ctx.ledger, run_id, ctx.result_of)) > 1
            if picks and ctx.cap_reached:
                texts.append(f"Daily cap reached — no more bets today. ({len(picks)} price gaps logged "
                             f"for the record.)")
            for n, again, r in picks if not ctx.cap_reached else []:
                league = ctx.league(next(x["sport_key"] for x in rows if x["source_event_id"] == r["source_event_id"]))
                args = (r, n, again, ctx.teams(r["event_ticker"]), league, now, several)
                earlier = ctx.earlier_fair(r)
                text = pick_text(*args, earlier=earlier)
                if len(f"{text}\n{ctx.footer()}") > MESSAGE_MAX:
                    text = pick_text(*args, compact=True, earlier=earlier)
                texts.append(text)
            if picks and not ctx.cap_reached:
                combo = combo_line([(n, r) for n, _, r in picks], ctx.table)
                if combo:
                    texts.append(combo)
            if not picks and compared:
                texts.append(f"{slot(ctx, cstart, keys_of('checked', lambda r: r['source_event_id'] in compared))}"
                             f": checked, no price gaps · prices {age} min old")
            if checked - compared:
                texts.append(f"Don't bet now: prices not checked (could not compare). "
                             f"{slot(ctx, cstart, keys_of('checked', lambda r: r['source_event_id'] not in compared))}")
    if by.get("no_price"):
        texts.append(f"{slot(ctx, cstart, keys_of('no_price'))}: no sharp price available, not checked.")
    if by.get("fee_unknown") and ctx.fee_state != "ok":
        extra.append((f"{display_date(now)}|nofeetable", ["Don't bet today: prices not checked (no fee table)."]))
    elif by.get("fee_unknown"):
        leagues = sorted({ctx.league(k) for k in keys_of("fee_unknown")})
        texts.append(f"Don't bet now: prices not checked (fee not confirmed for {', '.join(leagues)}). "
                     f"{slot(ctx, cstart, keys_of('fee_unknown'))}")
    if by.get("no_credits"):
        later = [c for c in checks.plan(ctx.store, SOURCE, now, pacific_day(now)[1]) if c.start_utc >= cstart]
        extra.append((f"{display_date(now)}|nocredits",
                      [f"Out of odds credits today: {len(later)} later time slots not checked — don't bet them."]))
    return [(f"{iso_z(cstart)}|{offset}|{run_id}", texts)] + [(k, t) for k, t in extra if k not in sent]


def alert_messages(ctx, sent):
    """[(key, [texts])] not yet sent: replies that named no pick, checks that ran, and due
    checks a failing system missed."""
    now, store = ctx.now, ctx.store
    out = [(f"unclear|{guid}", [unclear_text(text)]) for guid, text in ctx.replies["unclear_lines"]
           if f"unclear|{guid}" not in sent]
    groups = checks.recent_checks(store, SOURCE, now - timedelta(hours=3))
    seen_keys = set(sent)
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
    if ctx.snapshot_failing or store.last_finish("snapshot")[0] == "failed":
        done = {(r["source_event_id"], r["offset_min"]) for grp in groups.values() for r in grp}
        plan = checks.plan(store, SOURCE, now, now + timedelta(hours=24))
        for c, offset in checks.missed_checks(plan, done, now, checks.offsets()):
            key = f"{iso_z(c.start_utc)}|{offset}|missed"
            if key not in seen_keys:
                out.append((key, [f"Don't bet now: prices not checked (system problem). "
                                  f"{slot(ctx, c.start_utc, [g.sport_key for g in c.games])}"]))
                seen_keys.add(key)
    return out


# Morning ----------------------------------------------------------------------------

def problems(ctx):
    out = []
    state = health.read_state()
    for job in ("snapshot", "grader"):
        if state.get(job, {}).get("consecutive_failures"):
            out.append(f"{job} failing")
    last_grader = ctx.store.con.execute(
        "SELECT summary FROM job_runs WHERE job = 'grader' AND phase = 'finish' ORDER BY ts_utc DESC LIMIT 1").fetchone()
    if last_grader and json.loads(last_grader[0] or "{}").get("my_bets_settled") == "error":
        out.append("own bets step failed")
    _, last = ctx.store.last_finish("snapshot")
    if last is None or ctx.now - last > NOT_RUNNING_AFTER:
        out.append("prices not updating" if last else "no price run yet")
    if ctx.fee_state != "ok":
        out.append(f"fee table {ctx.fee_state}")
    if not config.odds_api_key():
        out.append("odds not set up")
    if not ctx.replies.get("readable"):
        out.append("bet log unreadable")
    return out


def unable_reason(ctx):
    """Why no check could have run (rule 8: name the cause, never print a bare zero), or None."""
    if not config.odds_api_key():
        return "odds not set up"
    return {"missing": "no fee table", "invalid": "fee table invalid"}.get(ctx.fee_state)


def today_line(ctx, now):
    cl = checks.plan(ctx.store, SOURCE, *pacific_day(now))
    if not cl:
        return "Today: no matched games" + (" (odds not set up)" if not config.odds_api_key() else "")
    slots = [slot(ctx, c.start_utc, [g.sport_key for g in c.games]).replace(" · ", " ", 1) for c in cl]
    shown = " · ".join(slots[:6]) + (f" · +{len(slots) - 6} more in the sheet" if len(slots) > 6 else "")
    return f"Today: {sum(len(c.games) for c in cl)} games, {len(cl)} time slots (PT): {shown}"


def yesterday_lines(ctx, now):
    y0, y1 = pacific_day(now, 1)
    day = display_date(y0)
    checked = checks.checked_slots(ctx.store, SOURCE, y0, y1)
    picks = [r for _, r in bets.day_picks(ctx.ledger, day, ctx.result_of).values()]
    won, lost, void, open_, money, staked = grader.paper_results(ctx.store, picks)
    cannot = unable_reason(ctx)
    if not checked and not picks and cannot:
        line = f"Yesterday: nothing checked ({cannot})"
    else:
        line = f"Yesterday: {checked} checks, {len(picks)} price gaps in {len({r['event_ticker'] for r in picks})} games"
    if picks:
        line += f" · results: {won} won, {lost} lost, {open_} open" + (f", {void} void" if void else "")
        if won + lost:
            line += f" · on paper {dollars(money)} on ${staked:.0f}"
    lines = [line]
    if not ctx.replies.get("readable"):
        lines.append("Your bets: bet log unreadable")
    else:
        mine = [b for b in ctx.taken if b["pick_date"] == day]
        skipped = sum(1 for r in grader.latest_replies(ledger.read_rows(config.bet_log_path())).values()
                      if r["action"] == "skip" and r["pick_date"] == day)
        settled = [b["dollars"] for b in mine if b["dollars"] is not None]
        lines.append(f"You took {len(mine)}" + (f": {dollars(sum(settled, Decimal(0)))}" if settled else "")
                     + (f" ({len(mine) - len(settled)} open)" if len(settled) < len(mine) else "")
                     + f" · skipped {skipped}")
    if config.odds_plan() == "free":
        lines.append(unchecked_line(ctx, y0, y1))
    return lines


def unchecked_line(ctx, y0, y1):
    games, missed, slots = checks.unchecked(ctx.store, SOURCE, y0, y1)
    if not games:
        why = " (odds not set up)" if not config.odds_api_key() else ""
        return f"Unchecked yesterday: no matched games yesterday{why}"
    if not missed:
        return f"Unchecked yesterday: none (all {len(games)} games checked)"
    why = {}
    for _, status in missed:
        why[UNCHECKED_WORDS[status]] = why.get(UNCHECKED_WORDS[status], 0) + 1
    return (f"Unchecked yesterday: {len(missed)} games in {slots} time slots ("
            + ", ".join(f"{k} {v}" for k, v in sorted(why.items())) + ")")


def my_bets_line():
    """The owner's own bets (docs/specs/my_bets_spec.md); never part of the stage line, and never stops a run."""
    try:
        return _my_bets_line()
    except Exception as exc:
        return f"Your own bets: can't be read ({type(exc).__name__})"


def _my_bets_line():
    logged, won, lost, void, stuck, net, _unreadable = grader.my_bets_summary()
    if not logged:
        return "Your own bets: none logged yet."
    extra = ", ".join(x for x in (f"{void} void" if void else "", f"{stuck} can't settle" if stuck else "") if x)
    money = f", {dollars(net)}" if won + lost else ""          # no dollar figure from nothing settled (rule 8)
    return f"Your own bets: {logged}, settled {won + lost}{money}." + (f" ({extra})" if extra else "")


def morning_text(ctx, now):
    trouble = problems(ctx)
    system = "System OK" if not trouble else "System: " + "; ".join(trouble)
    lines = [f"Kalshi slate · {display_day(now)}", ctx.stage_line, today_line(ctx, now)]
    lines += yesterday_lines(ctx, now)
    lines += [unclear_text(text) for _, text in ctx.replies["unclear_lines"][:2]]
    footer = f"{system} · full sheet: iCloud Drive/sports-edge/sheet.txt"
    text = "\n".join(lines + [my_bets_line(), footer])
    if len(text) > MORNING_MAX:                 # the own-bets line goes first (my_bets_spec.md, amendment 2026-10-23)
        text = "\n".join(lines + [footer])
    if len(text) > MORNING_MAX:
        raise ValueError(f"morning message is {len(text)} characters, over {MORNING_MAX}")
    return text


# Board ------------------------------------------------------------------------------

def board_line(ctx, r, now, number=None, why=""):
    """One gap: the pick text on one line with its number, or unnumbered ending with why not to bet."""
    league = ctx.league(checks.sport_key_of(ctx.store, r["market_ticker"]) or r["sport"])
    head, reply = pick_text(r, number, False, ctx.teams(r["event_ticker"]), league, now,
                            earlier=ctx.earlier_fair(r)).split("\n")
    return f"  {head} · {reply}" if number else f"  {head.split(' ', 1)[1]} · {why}"


def near_line(ctx, r):
    who, _ = wording(r, ctx.teams(r["event_ticker"]))
    return (f"  {who} · {ctx.league(r['sport_key'])} · {display_clock(parse_utc(r['commence_utc']))} · "
            f"now {cents(r['price_paid'])} · fair {cents(side_fair(r))} · gap {cents(r['edge'])} after fee · not a pick")


def board_lines(ctx, now):
    """The board (docs/specs/slate_alerts_spec.md): gaps from each game's latest check, near misses,
    and checks too old or unsafe to bet."""
    store = ctx.store
    starts = checks.latest_starts(store, SOURCE)
    current, older, why = {}, {}, {}
    for eid, check in board.latest_checks(store, SOURCE, now, starts).items():
        status, end, _ = store.run_finish(check["run_id"])
        if status != "ok" or ctx.snapshot_failing:
            older[eid], why[eid] = check, "system problem, don't bet"
        elif now - end > FRESH:
            older[eid], why[eid] = check, "old price, don't bet"
        else:
            current[eid] = check
    gaps = board.current_gaps(ctx.ledger, current, ctx.result_of, now, starts)
    late = board.current_gaps(ctx.ledger, older, ctx.result_of, now, starts)
    near = [] if ctx.fee_state != "ok" else board.near_misses(
        store, SOURCE, current, ctx.result_of, now, starts, {ctx.result_of(r) for r in gaps})
    more = lambda rows: [f"  +{len(rows) - BOARD_MAX} more"] if len(rows) > BOARD_MAX else []
    lines = [f"BOARD (price gaps as of each game's check, {display_clock(now)})"]
    if ctx.cap_reached:
        lines.append("  Daily cap reached — no more bets today.")
    for r in gaps[:BOARD_MAX]:
        n = None if ctx.cap_reached else bets.number_of(ctx.ledger, ctx.result_of(r), ctx.result_of)[1]
        lines.append(board_line(ctx, r, now, n, "cap reached, don't bet"))
    lines += more(gaps)
    if gaps and not ctx.cap_reached:
        combo = combo_line([(bets.number_of(ctx.ledger, ctx.result_of(r), ctx.result_of)[1], r) for r in gaps],
                           ctx.table)
        if combo:
            lines.append(f"  {combo}")
    if not gaps:
        cause = unable_reason(ctx) or ("no checked game still to start" if not current and not older else
                                       "only older checks, see below" if not current else
                                       "checked, no price gaps of 3¢ or more")
        lines.append(f"  none ({cause})")
    if near:
        lines += ["  Near misses (not picks):"] + [near_line(ctx, r) for r in near]
    if late:
        lines += ["  Older checks, don't bet:"] + [board_line(ctx, r, now, why=why[r["source_event_id"]])
                                                   for r in late[:BOARD_MAX]] + more(late)
    return lines


# Sheet ------------------------------------------------------------------------------

def sheet_lines(ctx, now):
    store = ctx.store
    status, last = store.last_finish("snapshot")
    newest = store.newest_snapshot()
    age = f"{display(newest)} ({minutes(now - newest)} min old)" if newest else "none yet"
    lines = [f"Slate sheet · built {display(now)}", ctx.stage_line,
             f"Newest price: {age} · last price run {display(last)} ({status or 'none'})",
             "Bet log: " + ("read" if ctx.replies.get("readable") else f"unreadable ({ctx.replies.get('error')})")
             + (f" · money at risk today ${ctx.at_risk:.0f} of ${bets.DAILY_CAP:.0f}"
                + (" · daily cap reached" if ctx.cap_reached else "")
                if ctx.replies.get("readable") else " · daily cap not tracked"),
             my_bets_line(),
             ""] + board_lines(ctx, now) + ["", "TODAY (Pacific time)"]
    start, end = pacific_day(now)
    cl = checks.plan(store, SOURCE, start, end)
    logged = checks.check_history(store, SOURCE, start - timedelta(hours=2))
    if not cl:
        lines.append("  no matched games" + ("" if config.odds_api_key() else " (odds not set up)"))
    for c in cl:
        lines.append(f"  {display_clock(c.start_utc)} slot · checks at "
                     + ", ".join(display_clock(checks.check_time(c, o)) for o in checks.offsets()))
        for g in c.games:
            done = "; ".join(f"{o} min before: {st} at {display_clock(ts)}" for o, st, ts in logged.get(g.source_event_id, []))
            lines.append(f"    {g.away} at {g.home} · {ctx.league(g.sport_key)} · {display_clock(g.commence_utc)}"
                         f" · {done or 'not checked yet'}")
    today = display_date(now)
    postponed = bets.postponed_picks(ctx.ledger, today, ctx.result_of, checks.latest_starts(store, SOURCE))
    if postponed:
        lines += ["", "POSTPONED"] + [f"  #{n} postponed — void (now listed {display(latest)})" for n, latest in postponed]
    lines += ["", "YOUR REPLIES TODAY"]
    replies = [r for r in ledger.read_rows(config.bet_log_path()) if r["pick_date"] == today]
    lines += [f"  #{r['pick_no'] or '?'} {r['action']}" + (f" at {cents(r['price'])}" if r["price"] else "")
              + f" ({r['raw_text']!r})" for r in replies] or ["  none"]
    return lines


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
    sent = load_sent()
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now)
        msgs = alert_messages(ctx, sent)
        sheet = sheet_lines(ctx, now)
    count = 0
    for key, texts in msgs:
        for i, text in enumerate(texts):
            part = f"{key}#{i}"
            if part in sent:         # already delivered before a failure partway through this group
                continue
            full = f"{text}\n{ctx.footer()}"
            if len(full) > MESSAGE_MAX:
                raise ValueError(f"alert is {len(full)} characters, over {MESSAGE_MAX}: {full}")
            send(full)
            count += 1
            sent[part] = iso_z(now)
            save_sent(sent, now)
        sent[key] = iso_z(now)
        save_sent(sent, now)
    path = write_sheet(sheet)
    out(f"SLATE ALERT OK messages={count} groups={len(msgs)} replies_logged={ctx.replies['logged']} "
        f"bet_log={'read' if ctx.replies['readable'] else 'unreadable'} sheet={path}")
    return count


def run_morning(now=None, send=imessage.send, out=print):
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now)
        text = morning_text(ctx, now)
        sheet = sheet_lines(ctx, now)
    send(text)
    path = write_sheet(sheet)
    out(text)
    out(f"SLATE MORNING OK sheet={path}")
    return text


def run_show(now=None, out=print):
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now, ingest=False)
        for line in sheet_lines(ctx, now):
            out(line)


def run_board(now=None, out=print):
    """Print the board. Writes nothing: no replies read, no sent state, no sheet."""
    now = now or utcnow()
    with Store.open(lock_wait_s=120) as store:
        ctx = Context(store, now, ingest=False)
        for line in board_lines(ctx, now):
            out(line)


def main(argv):
    command = {"alert": run_alert, "morning": run_morning, "show": run_show, "board": run_board}.get(
        argv[0] if argv else "")
    if command is None:
        print("usage: python -m sports_edge.slate show|board|alert|morning", file=sys.stderr)
        return 2
    command()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # noqa: BLE001
        print(f"SLATE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
