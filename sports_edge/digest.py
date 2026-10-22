"""Daily digest (docs/specs/daily_digest_spec.md, Part A).

python -m sports_edge.digest
First line: the digest line. Second line: "report: <path>". The full report
is written to a new file under logs/ and never overwritten. Exit 1 on any
exception; problems found are content, not a failure.
"""
import json
import sys
from datetime import timedelta

from . import config, fees, health, ledger
from .store import Store, from_db_ts, to_db_ts
from .timeutil import DISPLAY_TZ, display, display_date, parse_utc, utcnow

TRUST_N = 20
BANNER_MAX = 190   # notify.sh adds " → <file>" and cuts at 300 (220 on osascript)
SKIP_KEYS = ("skip_reference_stale", "skip_insufficient_depth")


def windows(now):
    """(yesterday_start, today_start) as aware datetimes at Pacific midnight."""
    today = now.astimezone(DISPLAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1), today


def first_fire(rows, order_key):
    """Earliest row per market + side + rule_id: the grader's bet unit."""
    first = {}
    for r in sorted(rows, key=lambda r: r[order_key]):
        first.setdefault((r["market_ticker"], r["side"], r["rule_id"]), r)
    return list(first.values())


def fee_table_state():
    try:
        return "ok" if fees.load_table() is not None else "missing"
    except fees.FeeTableInvalid:
        return "invalid"


def _money(graded_first):
    """(word, amount text, games) over graded first-fire bets: cents per $1 staked."""
    pnl = sum(float(r["pnl_per_contract"]) for r in graded_first)
    cost = sum(float(r["price_paid"]) + float(r["fee_per_contract"]) for r in graded_first)
    cents = round(100 * pnl / cost, 1) if cost else 0.0
    word = "up" if cents > 0 else "down" if cents < 0 else "even"
    return word, (f" {abs(cents):g}¢" if cents else ""), len({r["event_ticker"] for r in graded_first}), cents


def money_line(graded_first):
    if not graded_first:
        return "If you'd bet all of them: no finished bets yet", {}
    word, amount, n, cents = _money(graded_first)
    caveat = f" — too early to trust under {TRUST_N}" if n < TRUST_N else ""
    return (f"If you'd bet all of them: {word}{amount} per $1 ({n} game{'s' if n != 1 else ''}{caveat})",
            {"bets": len(graded_first), "events": n, "cents_per_dollar": cents})


def clv_line(graded_first):
    have = [float(r["clv"]) for r in graded_first if r.get("clv") not in (None, "")]
    if not have:
        return "Beat the final pre-game market price: no closing prices yet"
    beat = sum(1 for c in have if c > 0)
    return f"Beat the final pre-game market price: {beat} of {len(have)} times"


def plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def banner(games, underpriced, scored, graded_first, problems):
    """The notification text (spec amendment: banner format). Words, never a
    zero, for a field whose path cannot produce a number."""
    system = "system OK" if not problems else "system problems"
    if not graded_first:
        return f"Sports-edge: no games scored yet · {system}"
    word, amount, n, _ = _money(graded_first)
    trust = "too early" if n < TRUST_N else plural(n, "game")
    return "Sports-edge: " + " · ".join(
        [games, underpriced, scored, f"{word}{amount} per $1 ({trust})", system])


def build_digest(now=None):
    """Return (digest_line, report_lines, banner)."""
    now = now or utcnow()
    y0, t0 = windows(now)
    in_yesterday = lambda ts: ts is not None and y0 <= ts < t0
    problems, detail = [], []

    state = health.read_state()
    for job in ("snapshot", "grader"):
        s = state.get(job, {})
        if s.get("consecutive_failures", 0):
            problems.append(f"{job} failing ({s['consecutive_failures']} in a row)")
        elif parse_utc(s.get("last_success_utc")) is None:
            problems.append(f"{job} has never succeeded")
    grader_ran = parse_utc(state.get("grader", {}).get("last_success_utc")) is not None

    with Store.open(lock_wait_s=120) as store:
        runs = store.con.execute(
            "SELECT ts_utc, status, summary FROM job_runs WHERE job = 'snapshot' AND phase = 'finish' "
            "AND ts_utc >= ? AND ts_utc < ?", [to_db_ts(y0), to_db_ts(t0)]).fetchall()
        sport_sql = ("SELECT coalesce(m.sport, 'unknown'), count(DISTINCT m.event_ticker) FROM price_snapshots p "
                     "JOIN markets m ON m.ticker = p.ticker WHERE p.ts_utc >= ? AND p.ts_utc < ? {} "
                     "GROUP BY 1 ORDER BY 2 DESC, 1")
        # Watched = games tracked against reference odds (the 15-minute and hourly
        # tiers). The daily full scan of every open event goes to the report only.
        by_sport = store.con.execute(sport_sql.format("AND p.tier IN ('near', 'hourly')"),
                                     [to_db_ts(y0), to_db_ts(t0)]).fetchall()
        scanned = store.con.execute(sport_sql.format(""), [to_db_ts(y0), to_db_ts(t0)]).fetchall()
        credits = store.con.execute(
            "SELECT coalesce(sum(credits_last), 0) FROM odds_calls WHERE ts_utc >= ? AND ts_utc < ?",
            [to_db_ts(y0), to_db_ts(t0)]).fetchone()[0]

    ok_runs = [r for r in runs if r[1] in ("ok", "exchange_closed")]
    if not ok_runs:
        problems.append("no snapshot run finished yesterday")
        watched = "Games watched yesterday: no snapshot run finished yesterday"
        b_games = "no snapshot run"
        skipped = "Skipped: no snapshot run finished yesterday"
    else:
        total = sum(n for _, n in by_sport)
        sports = ", ".join(f"{s} {n}" for s, n in by_sport)
        if by_sport:
            watched = f"Games watched yesterday: {total} ({sports})"
            b_games = plural(total, "game")
        else:
            watched = "Games watched yesterday: none matched to reference odds"
            b_games = "no matched games"
        detail.append(f"full scan yesterday: {sum(n for _, n in scanned)} open games seen across "
                      f"{len(scanned)} categories (not compared unless matched): "
                      + ", ".join(f"{c} {n}" for c, n in scanned))
        skips, checked = {k: 0 for k in SKIP_KEYS}, 0
        for _, _, summary in ok_runs:
            data = json.loads(summary or "{}")
            checked += int(data.get("shadow_checked", 0))
            for k in SKIP_KEYS:
                skips[k] += int(data.get(k, 0))
        if checked:
            skipped = f"Skipped: {sum(skips.values())} (prices too old, or not enough contracts for sale)"
        else:
            skipped = "Skipped: nothing checked (no games with reference odds)"
        detail.append(f"price checks yesterday: {checked}; skipped as too old {skips['skip_reference_stale']}, "
                      f"as too few contracts {skips['skip_insufficient_depth']}; over {len(ok_runs)} runs")

    fee_state = fee_table_state()
    logged = ledger.read_rows(config.shadow_ledger_path())
    new_first = [r for r in first_fire(logged, "snapshot_utc") if in_yesterday(parse_utc(r["logged_utc"]))]
    if fee_state != "ok":
        problems.append(f"fee table {fee_state}")
        spotted = f"Underpriced bets spotted: none possible — {'no' if fee_state == 'missing' else 'invalid'} fee table"
        b_under = "no fee table" if fee_state == "missing" else "fee table invalid"
    else:
        events = {r["event_ticker"] for r in new_first}
        spotted = f"Underpriced bets spotted: {len(events)}"
        b_under = f"{len(events)} underpriced"
        detail.append(f"new bets yesterday: {len(new_first)} across {len(events)} games")

    graded = ledger.read_rows(config.shadow_grades_path())
    if not grader_ran:
        scored = "Finished games scored: grader has never run"
        b_scored = "grader never ran"
    else:
        today_grades = [r for r in graded if t0 <= parse_utc(r["graded_utc"]) <= now]
        scored = f"Finished games scored: {len({r['event_ticker'] for r in today_grades})}"
        b_scored = f"{len({r['event_ticker'] for r in today_grades})} scored"
        detail.append(f"grades written since {display(t0)}: {len(today_grades)} rows")
    graded_first = first_fire(graded, "snapshot_utc")
    money, money_detail = money_line(graded_first)
    if money_detail:
        detail.append(f"money line basis: {money_detail['bets']} bets in {money_detail['events']} games "
                      f"(all graded so far)")

    if config.odds_api_key():
        odds = f"Odds credits used: {int(credits)} of {config.odds_daily_credits()}"
    else:
        odds = "Odds credits used: not configured"
        problems.append("odds not configured")

    system = "System: OK" if not problems else "System: problems — " + "; ".join(problems)
    line = " · ".join([watched, spotted, scored, money, clv_line(graded_first), skipped, system, odds])
    report = [
        line,
        "",
        f"Yesterday = {display(y0)} to {display(t0)}; scores counted from {display(t0)} to {display(now)}.",
        "Money and closing-price lines cover every graded bet so far. A bet is the first logged price "
        "per market, side and rule; a game counts once however many bets it has.",
        "Nothing here claims that a price gap is real.",
        "",
    ] + detail
    text = banner(b_games, b_under, b_scored, graded_first, problems)
    if len(text) > BANNER_MAX:
        raise ValueError(f"banner is {len(text)} characters, over {BANNER_MAX}: {text}")
    return line, report, text


def write_report(lines, now):
    base = config.logs_dir()
    base.mkdir(parents=True, exist_ok=True)
    stem = f"digest_{display_date(now)}"
    for i in range(1, 1000):
        path = base / (f"{stem}.txt" if i == 1 else f"{stem}_{i}.txt")
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"too many digest reports for {stem}")


def main():
    now = utcnow()
    line, report, text = build_digest(now)
    path = write_report(report, now)
    print(text)
    print(f"report: {path}")
    print(line)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"DIGEST FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
