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
`jobguard` calls `faulthandler.dump_traceback_later(<limit>, exit=True)` and
then runs the module as `__main__`. At the limit, every thread's stack goes
to stderr (so into the day log) and the process exits 1. The wrapper then
treats it like any other failure: `record-failure`, an ALERT, a nonzero
exit. The reason reads `stopped at the job time limit: Timeout (h:mm:ss)!`.
The module's own exit code is passed through unchanged below the limit. A
module with no limit in the table is refused (exit 2), never run unguarded.

**Revised 2026-10-24 (owner): one limit per job.** Each limit is the job's
own database lock wait plus the larger of 2× its slowest normal run and
120 s. The lock wait is added so that a job waiting for the lock still
fails with its own `StoreLocked` message, not the time limit. Normal runs
exclude catch-up runs and runs stretched by the Mac sleeping (OBSERVED in
`job_runs`, the day logs and `pmset -g log`, 2026-10-22 to 2026-10-24):

| Job (module) | Lock wait | Normal runs | Slowest normal | Left out | Limit |
|---|---|---|---|---|---|
| snapshot, until its tier is known | 600 s | — | — | — | 720 s |
| snapshot, near tier | (before) | 105 | 99 s | 607 s: the Mac slept from 10:57:41 to 11:07:29 PM PT, Oct 23, mid-run | 200 s |
| snapshot, hourly tier | (before) | 33 | 31 s | — | 120 s |
| snapshot, daily tier | (before) | 2 | 238 s | 1,103 s catch-up, 2026-10-24 9:30 AM PT | 2,200 s |
| grader | 600 s | 1 | 10 s | 14,107 s failed run | 720 s |
| slate (alert, morning) | 120 s | 1 morning | about 8 s | — | 240 s |
| heartbeat | 120 s | 0 | not measured | — | 240 s |
| health (the watch) | none | — | under 1 s | — | 120 s |
| backup | 600 s | 0 | not measured | — | 2,200 s |

- **The snapshot narrows its limit** once it knows its tier
  (`jobguard.set_limit`, counted from that moment): near 200 s, hourly
  120 s, daily 2,200 s. Before that, 720 s covers the lock wait and the
  exchange status call. `set_limit` does nothing outside `jobguard`.
- **The daily snapshot keeps 2,200 s, 2× the catch-up run, not 2× 238 s.**
  - Catch-ups are expected: the first daily run after any outage sweeps
    every settlement since the last daily, and the Mac has slept through
    the night.
  - A killed daily run is retried as a daily run, with the same sweep, so a
    limit below the catch-up would never let it finish.
  - Kalshi's speed varies: the Oct 23 daily averaged 1.0 s a request, the
    Oct 24 one 5.1 s (139 and 215 requests).
- **The heartbeat and backup** have no normal run to measure. The heartbeat
  gets the floor; the backup keeps 2,200 s until its first real runs.

**3. Every HTTPS GET has a deadline** (`sports_edge/net.py`, `https_get`;
the Kalshi and odds clients both use it). The stuck snapshot was reading a
chunked response body that kept arriving (the `sample` showed it alternating
between reading chunk-size lines and chunk data), so the 30 s socket timeout,
which restarts with every byte, never fired. `https_get` now reads the body
in pieces (`read1`) and raises `TimeoutError("response not complete within
120 s")` once the whole GET passes `DEADLINE_S` = 120 s. The slowest real GET
seen is 42 s (`/series`, 6.4 MB, 2026-10-24). The message never carries the
URL, because the odds API key is in its query. `KalshiClient.get` retries a
`TimeoutError` like any timeout, then fails with `KalshiError`. The deadline
covers the body. Connecting and reading the headers are still bounded only
by the socket timeout and `http.client`'s header limits.

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
- **60 minutes** covers a normal day: the slowest finished run seen
  (1,103 s) plus up to 15 minutes until the next scheduled start. A run
  that is stopped at its limit alerts by itself. Only a daily run that both
  waits out the lock and hangs (720 s + 2,200 s) could pass 60 minutes
  first; the watch then alerts too.
- When the Mac is asleep, launchd starts nothing, the watch included; the
  first watch run after the wake reports the gap.

**Tests.** The limit table and the deadline are pinned. Every module a
wrapper starts has a limit, and an unknown one is refused. `set_limit`
restarts the limit inside `jobguard` and does nothing outside it. Each
snapshot tier sets its own limit. A trickling body gives up at the
deadline, on a fake clock and on a real localhost socket, and the real
`KalshiClient` gives up after its retries. A module that blocks forever exits 1 within the limit, through
`jobguard` alone and through `run_job`, with the stack and the
time-limit reason in the alert. A module that finishes keeps its exit code
and arguments. Every wrapper starts its module through `jobguard`. The
stale check: fresh, stale, a recent failure counts as a finished run, and
nothing recorded is stale. The watch wrapper alerts once, then recovers.
