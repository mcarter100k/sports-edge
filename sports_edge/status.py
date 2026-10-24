"""What the system is doing, in at most five lines (docs/specs/status_spec.md).

python -m sports_edge.status

Reads only: the database is opened read-only and nothing is written.
"""
import sys
from collections import Counter

from . import checks, config, health, slate
from .bets import day_picks
from .store import Store
from .timeutil import display_clock, display_date, pacific_day, parse_utc, utcnow

JOBS = {"snapshot": "price check", "slate_alert": "pick alerts", "grader": "nightly grading",
        "heartbeat": "weekly check"}
UNCHECKED = {"no_credits": "out of odds credits", "fee_unknown": "fee not confirmed", "no_price": "no sharp price",
             "superseded": "checks not run"}
NO_RUN_YET = "Problem: no price check has run yet, see logs/"


def health_line(store, now):
    failing = [(parse_utc(s["last_failure_utc"]), job) for job, s in health.read_state().items()
               if s.get("consecutive_failures") and s.get("last_failure_utc")]
    if failing:
        at, job = max(failing)
        return f"Problem: {JOBS.get(job, job)} failed at {display_clock(at)}, see logs/{job}_{display_date(at)}.log"
    _, last = store.last_finish("snapshot")
    if last is None:
        return NO_RUN_YET
    if now - last > slate.NOT_RUNNING_AFTER:
        return f"Problem: no price check since {display_clock(last)}, see logs/snapshot_{display_date(last)}.log"
    return f"Running fine · last check {display_clock(last)}"


def why_no_picks(ctx, statuses):
    """The single main reason a day with checks has no picks."""
    reason = {"missing": "no fee table", "invalid": "fee table invalid"}.get(ctx.fee_state)
    if reason or not config.odds_api_key():
        return reason or "odds not set up"
    if "checked" not in statuses:
        return UNCHECKED.get(Counter(statuses).most_common(1)[0][0], "checks not run")
    return "no price gaps of 3¢ or more"


def status_lines(store, now):
    ctx = slate.Context(store, now, ingest=False)
    start, end = pacific_day(now)
    games = {g.source_event_id for c in checks.plan(store, slate.SOURCE, start, end) for g in c.games}
    history = checks.check_history(store, slate.SOURCE, start)
    statuses = [st for eid in games for _, st, _ in history.get(eid, [])]
    checked = sum(1 for eid in games if any(st == "checked" for _, st, _ in history.get(eid, [])))
    picks = len(day_picks(ctx.ledger, display_date(now), ctx.result_of))
    lines = [f"SPORTS-EDGE · {display_clock(now)}", health_line(store, now),
             f"Today: {len(games)} games tracked · {checked} checked · {picks} picks"]
    if picks == 0 and statuses:
        lines.append(f"Why no picks: {why_no_picks(ctx, statuses)}")
    lines.append(f"Odds credits: {store.credits_used_since(start)} of {config.odds_daily_credits()} used today")
    return lines


def run_status(now=None, out=print):
    now = now or utcnow()
    if not config.db_path().exists():
        lines = [f"SPORTS-EDGE · {display_clock(now)}", NO_RUN_YET]
    else:
        with Store.open(lock_wait_s=120, read_only=True) as store:
            lines = status_lines(store, now)
    for line in lines:
        out(line)
    return lines


def main():
    run_status()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"STATUS FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
