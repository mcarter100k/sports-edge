# Spec: scheduled jobs, alerts and heartbeat (`scripts/*.sh`, `sports_edge/health.py`)

Status: Phase 1. Protected files.

## Every scheduled wrapper (`run_snapshot.sh`, `run_grader.sh`, `weekly_heartbeat.sh`)

1. `cd /Users/quis/sports-edge`. Read `.env` with `set -a; . ./.env; set +a`
   unless `SPORTS_EDGE_NO_ENV_FILE` is set (tests only). If `.env` is
   missing, the run fails.
2. Run `venv/bin/python -m sports_edge.<job>`, with stdout and stderr
   captured separately and both appended to `logs/<job>_<YYYY-MM-DD>.log`
   (the date in America/Los_Angeles).
3. **A run has failed** if the exit code is nonzero, stderr contains
   `Traceback (most recent call last)`, or stdout is empty.
4. **On failure:** call `health.py record-failure <job> <reason>`. The
   wrapper then **exits nonzero**: the Python exit code, or 1 if that was 0.
   A failed run never exits 0.
5. **On success:** call `health.py record-success <job>`, and exit 0.
6. The wrapper is plain bash 3.2 (launchd runs `/bin/bash`), and a test runs
   it under `/bin/bash`.

## When an alert reaches the owner (`health.py`)

State is in `state/health.json`. Per job:

| Situation | Notification |
|---|---|
| first failure after a success (or the first run ever) | **ALERT** `<job> failed: <reason>`, loud (sound) |
| still failing, ≥ 60 min since the last alert for this job | **ALERT** `<job> still failing (<n> in a row): <reason>` |
| still failing, < 60 min since the last alert | none (counted) |
| first success after failures | **RECOVERED** `<job> ok after <n> failures` |
| success after success | none |

This keeps a broken 15-minute job to at most one alert an hour, so the
alert channel never screams constantly and get ignored. Tests cover every
row of this table, including the ones that must **not** notify.

## Delivery (`scripts/notify.sh`)

`terminal-notifier` (at `~/Applications/terminal-notifier.app`, run with a
15 s watchdog) with sound `Basso`. If it is missing, fails or hangs, it falls
back to `osascript display notification … sound name "Basso"`. Every attempt
is appended to `logs/alerts.log`. If both methods fail, the wrapper writes
`ALERT DELIVERY FAILED` to the day log, and the heartbeat reports it.

## Forced-failure switch (proves the real launchd path)

If `state/force_failure_once` exists when `run_snapshot.sh` starts, the
wrapper deletes the file and runs that one cycle with
`KALSHI_BASE_URL=https://kalshi-forced-failure.invalid/trade-api/v2`. The
run fails on DNS. It writes no data, alerts through the normal path and
exits nonzero. The next scheduled run recovers and sends RECOVERED.

## Weekly proof of life (`weekly_heartbeat.sh`, Sundays 09:00 America/Los_Angeles)

It **always** notifies, healthy or not, so a missing Sunday notification is
itself the alarm. The report (also written to `logs/heartbeat_<date>.log`)
covers:

- each job: last success (LA time), consecutive failures;
- snapshot runs in the last 7 days, expected vs actual (672 expected);
  a shortfall over 5% is flagged;
- sports with snapshot rows in the last 24 h (Phase 1 needs at least 3);
- shadow rows and graded rows in the last 7 days; unmatched count;
- odds: configured or not, and credits remaining (last seen);
- database size and free disk (under 5 GB is flagged);
- system time zone (anything other than America/Los_Angeles is flagged).

Its first line is `healthy` or `NEEDS ATTENTION | <first problem>`. With
NEEDS ATTENTION it notifies loudly and **exits 1**.

---

## Amendment 2026-10-22

The report adds: matched markets by sport; sports with near or hourly
(matched) snapshots in the last 24 h. That is the Phase 1 "3+ sports" count,
since unmatched markets are now daily only. It also adds runs recorded as
`exchange_closed` in 7 days (flagged above 12), and the fee table state:
present or not, and how many series are unconfirmed.

## Amendment 2026-10-24: a hard time limit on every job, and a stale-snapshot watch

**Why.** On 2026-10-24 a snapshot run started at 3:38 AM PT and was still
waiting on one Kalshi HTTPS read at 9:27 AM PT, when it was killed. The
grader that started at the same time ran for 3 h 55 min before failing. A
run that never finishes never reaches `record-failure`, so no alert fired
for the stall, and launchd started no snapshot for almost six hours. The
morning job then failed on the database lock the stuck run still held.

**1. Every job module runs under `sports_edge/jobguard.py`.** The wrappers
start `python -m sports_edge.jobguard <module> [args]` in place of
`python -m <module> [args]`, for the snapshot, the slate alert, the grader,
the backup, the morning message, the heartbeat and the watch below.
`jobguard` calls `faulthandler.dump_traceback_later(LIMIT_S, exit=True)` and
then runs the module as `__main__`. At the limit, every thread's stack goes
to stderr (so into the day log) and the process exits 1. The wrapper then
treats it like any other failure: `record-failure`, an ALERT, a nonzero
exit. The reason reads `stopped at the job time limit: Timeout (h:mm:ss)!`.
The module's own exit code is passed through unchanged below the limit.

**`LIMIT_S` = 2,200 s (36 min 40 s), one value for every job:** about 2× the
slowest normal run on record (OBSERVED in `job_runs` and the day logs,
2026-10-22 to 2026-10-24):

| Job | Normal runs | Slowest |
|---|---|---|
| snapshot, daily tier | 3 | 1,103 s (9:30 AM PT 2026-10-24, catching up after the six-hour gap) |
| snapshot, near tier | 106 | 607 s; median 1 s |
| snapshot, hourly tier | 33 | 31 s; median 1 s |
| grader | 1 | 10 s |
| morning | 1 | about 8 s |
| slate alert | — | about 6 s (2026-10-24 9:48 AM PT; not recorded in `job_runs`) |
| heartbeat, backup | 0 | never run under launchd |

The daily catch-up run sets the limit: a limit below it would kill the run
that ends an outage. The other jobs are far below it.

**Limits of the limit.** `os._exit` skips buffered stdout, so a timed-out
run's stdout may be cut short; stderr holds the stack. A run stopped
mid-write leaves the database to DuckDB's WAL recovery, as a crash or power
loss would. The ledger's own lock, header check and fsync are unchanged.

**2. A stale-snapshot watch that does not depend on the snapshot job.** A new
launchd job, `com.quis.sportsedge.watch` (`scripts/run_watch.sh`), runs at
minutes 7, 22, 37 and 52. It runs
`python -m sports_edge.health check-stale snapshot` through `run_job` under
the job name `snapshot_watch`. It reads only `state/health.json`, never the
database, so a run holding the database lock cannot block it.

- **Stale** means no snapshot run has finished, ok or failed, for more than
  60 minutes (the later of `last_success_utc` and `last_failure_utc`), or no
  run is recorded at all. A run that finishes and fails already alerts, so
  the watch alerts only when runs stop finishing.
- **Stale:** the check writes
  `SNAPSHOT STALE: no run finished since <LA time> (<n> min)` to stderr and
  exits 1. `run_job` then alerts with the usual table: the first time loudly,
  then at most hourly, and RECOVERED once runs finish again.
- **60 minutes** covers the longest gap the limit allows: a run that hits
  the limit (36 min 40 s) plus up to 15 minutes until the next scheduled
  start, plus that run's own time.
- When the Mac is asleep, launchd starts nothing, the watch included; the
  first watch run after the wake reports the gap.

**Tests.** A module that blocks forever exits 1 within the limit, through
`jobguard` alone and through `run_job`, with the stack and the
time-limit reason in the alert. A module that finishes keeps its exit code
and arguments. Every wrapper starts its module through `jobguard`. The
stale check: fresh, stale, a recent failure counts as a finished run, and
nothing recorded is stale. The watch wrapper alerts once, then recovers.
