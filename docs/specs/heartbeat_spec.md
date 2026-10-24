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

## Amendment 2026-10-24: a hard time limit on every job, and a deadline on every GET

**Why.** On 2026-10-24 a snapshot run started at 3:38 AM PT was still
reading one Kalshi response at 9:27 AM PT, when it was killed. A run that
never finishes never reaches `record-failure`, so no alert fired, launchd
started no snapshot for almost six hours, and the 8 AM morning job failed
on the database lock the stuck run held.

**1. Every job module runs under `sports_edge/jobguard.py`.** The wrappers
start `python -m sports_edge.jobguard <module> [args]` in place of
`python -m <module> [args]`. `jobguard` calls
`faulthandler.dump_traceback_later(<limit>, exit=True)`, then runs the module
as `__main__`. At the limit, every thread's stack goes to stderr (so into the
day log) and the process exits 1; the wrapper alerts as for any failure,
with the reason `stopped at the job time limit: Timeout (h:mm:ss)!`. Below
the limit the module's own exit code passes through.

- **Two limits (owner decision):** `sports_edge.snapshot` and
  `sports_edge.grader` 2,200 s; every other module 120 s. 2,200 s is 2x the
  slowest snapshot run on record, the 1,103 s daily run of 2026-10-24 that
  caught up after the outage. The grader runs the snapshot's settlement
  sweep against the same slow Kalshi listing: on 2026-10-24 its catch-up run
  took 175 s, so 120 s stopped it before it graded anything.
- **Trade-off:** the slate and heartbeat wait up to 120 s for the database
  lock, so a long lock wait now ends at the time limit instead of with
  `StoreLocked`. Either way the run fails and alerts.
- `os._exit` skips buffered stdout, so a stopped run's stdout may be cut
  short; stderr holds the stack. A run stopped mid-write leaves the
  database to DuckDB's WAL recovery, as a crash would.

**2. Every HTTPS GET has a deadline** (`sports_edge/net.py`, `https_get`,
used by the Kalshi and odds clients). The stuck run was reading a chunked
body that kept arriving (its `sample` alternated between chunk-size lines
and chunk data), so the 30 s socket timeout, which restarts with every
byte, never fired. `https_get` reads the body in pieces (`read1`) and
raises `TimeoutError("response not complete within 120 s")` once the whole
GET passes `DEADLINE_S` = 120 s. The slowest real GET seen was 42 s
(`/series`, 6.4 MB, 2026-10-24). The message never carries the URL, since
the odds key is in its query. `KalshiClient.get` retries it like any
timeout, then fails with `KalshiError`. Connecting and the headers are still
bounded only by the socket timeout and `http.client`'s header limits.

**Tests.** A module that blocks forever exits 1 within the limit, through
`jobguard` alone and through `run_job`, with its stack and the time-limit
reason in the alert. A module that finishes keeps its exit code and
arguments. Every wrapper starts its module through `jobguard`. The two
limits and the deadline are pinned. A trickling body gives up at the
deadline (fake clock, and a real localhost socket), and the real
`KalshiClient` gives up after its retries.
