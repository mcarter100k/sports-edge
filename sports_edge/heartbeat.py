"""Weekly proof-of-life report (docs/specs/heartbeat_spec.md).

python -m sports_edge.heartbeat
First line: "healthy" or "NEEDS ATTENTION | <first problem>". Exit 1 when
attention is needed.
"""
import os
import shutil
import sys
from datetime import timedelta

from . import config, fees, health, ledger
from .store import Store, from_db_ts, to_db_ts
from .timeutil import display, iso_z, parse_utc, utcnow

EXPECTED_SNAPSHOT_RUNS = 7 * 24 * 4
SHORTFALL = 0.05
MIN_SPORTS = 3
MIN_FREE_BYTES = 5 * 1024 ** 3
EXPECTED_TZ = "America/Los_Angeles"
MAX_CLOSED_RUNS = 12   # Kalshi's weekly maintenance window is 8 runs


def system_timezone():
    try:
        return os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
    except (IndexError, OSError):
        return "unknown"


def build_report(now=None, tz_name=None, free_bytes=None):
    now = now or utcnow()
    week_ago, day_ago = now - timedelta(days=7), now - timedelta(days=1)
    lines, problems = [], []
    state = health.read_state()
    for job in ("snapshot", "grader"):
        s = state.get(job, {})
        last_ok = parse_utc(s.get("last_success_utc"))
        fails = s.get("consecutive_failures", 0)
        lines.append(f"{job}: last success {display(last_ok)}, {fails} consecutive failure(s)")
        if fails:
            problems.append(f"{job} failing ({fails} in a row): {s.get('last_reason', '')[:80]}")
        elif last_ok is None:
            problems.append(f"{job} has never succeeded")

    with Store.open(lock_wait_s=120) as store:
        q = lambda sql, *a: store.con.execute(sql, list(a)).fetchone()[0]
        runs_ok = q("SELECT count(*) FROM job_runs WHERE job='snapshot' AND phase='finish' "
                    "AND status='ok' AND ts_utc >= ?", to_db_ts(week_ago))
        runs_failed = q("SELECT count(*) FROM job_runs WHERE job='snapshot' AND phase='finish' "
                        "AND status='failed' AND ts_utc >= ?", to_db_ts(week_ago))
        first_run = from_db_ts(q("SELECT min(ts_utc) FROM job_runs WHERE job='snapshot'"))
        expected = EXPECTED_SNAPSHOT_RUNS
        if first_run and first_run > week_ago:
            expected = int((now - first_run) / timedelta(minutes=15))
        runs_closed = q("SELECT count(*) FROM job_runs WHERE job='snapshot' AND phase='finish' "
                        "AND status='exchange_closed' AND ts_utc >= ?", to_db_ts(week_ago))
        lines.append(f"snapshot runs (7 days): {runs_ok} ok, {runs_failed} failed, "
                     f"{runs_closed} exchange closed, {expected} expected")
        if expected and runs_ok + runs_closed < expected * (1 - SHORTFALL):
            problems.append(f"SHORTFALL: {runs_ok + runs_closed} of {expected} expected snapshot runs completed")
        if runs_closed > MAX_CLOSED_RUNS:
            problems.append(f"exchange reported closed on {runs_closed} runs in 7 days (weekly window is 8)")
        sports_all = [r[0] for r in store.con.execute(
            "SELECT DISTINCT m.sport FROM price_snapshots p JOIN markets m ON m.ticker = p.ticker "
            "WHERE p.ts_utc >= ? ORDER BY 1", [to_db_ts(day_ago)]).fetchall()]
        sports = [r[0] for r in store.con.execute(
            "SELECT DISTINCT m.sport FROM price_snapshots p JOIN markets m ON m.ticker = p.ticker "
            "WHERE p.ts_utc >= ? AND p.tier IN ('near', 'hourly') ORDER BY 1", [to_db_ts(day_ago)]).fetchall()]
        lines.append(f"sports with any snapshot (24 h): {len(sports_all)}")
        lines.append(f"sports with matched 15-min/hourly snapshots (24 h): {len(sports)} — {', '.join(sports[:12])}")
        if len(sports) < MIN_SPORTS:
            problems.append(f"only {len(sports)} sport(s) with matched snapshots in 24 h (need {MIN_SPORTS})")
        mbs = store.con.execute("""
            SELECT m.sport, count(DISTINCT m.ticker) FROM odds_matches o
            JOIN markets m ON m.ticker = o.market_ticker LEFT JOIN settlements s ON s.ticker = m.ticker
            WHERE s.ticker IS NULL GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        lines.append("matched open markets by sport: " + (", ".join(f"{k}={v}" for k, v in mbs) or "none"))
        settled = q("SELECT count(*) FROM settlements WHERE recorded_utc >= ?", to_db_ts(week_ago))
        unmatched = q("SELECT count(*) FROM unmatched WHERE ts_utc >= ?", to_db_ts(week_ago))
        matched = q("SELECT count(*) FROM odds_matches")
        lines.append(f"settlements recorded (7 days): {settled}; matched markets: {matched}; "
                     f"unmatched lines (7 days): {unmatched}")
        remaining = store.con.execute(
            "SELECT credits_remaining, ts_utc FROM odds_calls WHERE credits_remaining IS NOT NULL "
            "ORDER BY ts_utc DESC LIMIT 1").fetchone()
    key = config.odds_api_key()
    if not key:
        lines.append("odds: NOT CONFIGURED (Phase 1 needs reference odds)")
        problems.append("odds NOT CONFIGURED")
    else:
        lines.append("odds: configured; credits remaining "
                     + (f"{remaining[0]} (as of {display(from_db_ts(remaining[1]))})"
                        if remaining else "unknown (no call recorded yet)"))
    shadow = [r for r in ledger.read_rows(config.shadow_ledger_path())
              if parse_utc(r["logged_utc"]) >= week_ago]
    graded = [r for r in ledger.read_rows(config.shadow_grades_path())
              if parse_utc(r["graded_utc"]) >= week_ago]
    lines.append(f"shadow rows (7 days): {len(shadow)} logged, {len(graded)} graded")
    try:
        table = fees.load_table()
        if table is None:
            lines.append("fee table: MISSING (every fee fails closed; no shadow rows possible)")
            problems.append("fee table MISSING")
        else:
            lines.append(f"fee table: effective {table['source']['effective']}, "
                         f"{len(table.get('series') or {})} series overrides, "
                         f"{len(table.get('unconfirmed') or {})} unconfirmed (fail closed)")
    except fees.FeeTableInvalid as exc:
        lines.append(f"fee table: INVALID ({exc})")
        problems.append(f"fee table INVALID: {exc}")
    db = config.db_path()
    size = db.stat().st_size if db.exists() else 0
    free = free_bytes if free_bytes is not None else shutil.disk_usage(config.data_dir()).free
    lines.append(f"database {size / 1024 ** 2:.0f} MB; free disk {free / 1024 ** 3:.1f} GB")
    if free < MIN_FREE_BYTES:
        problems.append(f"free disk {free / 1024 ** 3:.1f} GB is under 5 GB")
    tz = tz_name or system_timezone()
    lines.append(f"system time zone: {tz}")
    if tz != EXPECTED_TZ:
        problems.append(f"system time zone is {tz}, schedules assume {EXPECTED_TZ}")
    alerts = config.logs_dir() / "alerts.log"
    if alerts.exists():
        cutoff = iso_z(week_ago)
        failed = [ln for ln in alerts.read_text(errors="replace").splitlines()
                  if "ALERT DELIVERY FAILED" in ln and ln[:19] >= cutoff[:19]]
        if failed:
            problems.append(f"ALERT DELIVERY FAILED {len(failed)} time(s): {failed[-1][:80]}")
    head = "healthy" if not problems else f"NEEDS ATTENTION | {problems[0]}"
    return [head, f"as of {display(now)}"] + lines + [f"problem: {p}" for p in problems], bool(problems)


def main():
    lines, attention = build_report()
    print("\n".join(lines))
    return 1 if attention else 0


if __name__ == "__main__":
    sys.exit(main())
