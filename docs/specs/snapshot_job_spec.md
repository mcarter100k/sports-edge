# Spec: snapshot cycle (`sports_edge/snapshot.py`, `scripts/run_snapshot.sh`)

Status: Phase 1. Protected files.

## Schedule

launchd `StartCalendarInterval` at minutes 0, 15, 30 and 45. launchd
interprets calendar times in the **system time zone**, which must be
America/Los_Angeles (true on 2026-10-21: `/etc/localtime` points to
`America/Los_Angeles`). The weekly heartbeat checks this and flags any
other zone.

## Tiers

| Tier | When | Which markets get a snapshot row |
|---|---|---|
| `full` | the :00 run, or any run when no `full` run has succeeded in the last 55 minutes (catch-up) | every open sports market in the scan |
| `near` | every other run | markets whose **resolve-by time** is within 48 h |

**Resolve-by time** = the earlier of `expected_expiration_time` and
`close_time`. Why not `close_time` alone: on game markets `close_time` is a
late backstop. For example, `KXNBAGAME-26OCT22CLEPHI-CLE` has
`expected_expiration_time` 2026-10-23T02:00Z but `close_time`
2026-10-24T23:00Z, which is 45 h after the game ends. Read literally,
"closing within 48 h" would leave tonight's games in the hourly tier.

## One run

1. Write a `job_runs` start row.
2. **Discover series:** on `full` runs (or if the `series` table is empty),
   `GET /series?category=Sports`, then upsert.
3. **Scan:** `GET /events?status=open&with_nested_markets=true`, every page.
   Keep events whose `series_ticker` is a sports series.
4. **Sanity:** if the scan returns 0 sports events or 0 sports markets, the
   run FAILS (`SCAN EMPTY`). A zero from an unproven path is not data.
5. **Write (one transaction):** upsert `markets`. Append `price_snapshots`
   for the tier. Insert `settlements` for any `finalized` market in the
   scan.
6. **Settlement sweep (full runs):**
   `GET /markets?status=settled&min_settled_ts=<last sweep − 2 h>`. Keep
   tickers present in `markets` and insert the finalized ones. For known
   markets past their resolve-by time by more than 6 hours with no
   settlement, and that are no longer active or have not been seen in a
   scan for 2 hours, fetch `GET /markets/{ticker}` (then
   `/historical/markets/{ticker}`), at most 200 per run, matched markets
   first. A market still listed as active is still open, so looking it up
   would be wasted. The first reality run spent 200 lookups that way.
7. **Odds + shadow:** run the odds step and the shadow comparison (see
   `odds_matching_spec.md` and `shadow_ledger_spec.md`). If no odds key is
   configured, the run prints `ODDS NOT CONFIGURED` and carries on. The
   heartbeat flags this, because Phase 1 needs odds.
8. Write a `job_runs` finish row with counts: events, markets, snapshot rows,
   sports covered, settlements added, matches, unmatched, shadow rows.
9. Print a one-line summary. Exit 0 only if every step above succeeded.

## Failure

Any exception means exit code 1 and an alert through the wrapper (see
`heartbeat_spec.md`). A failed odds step fails the run. The snapshot rows
already committed in step 5 stay, because they are valid observations.

---

## Amendment 2026-10-22: tiers by match status (owner-approved)

| Kind | When | Fetch | Snapshot rows for |
|---|---|---|---|
| `daily` | the 03:00 Los Angeles run, or any run when no `daily` run has succeeded in the last 24 h 15 min | full scan of open sports events, then series refresh and settlement sweep | every open sports market |
| `hourly` | other :00 runs | **matched events only**: `GET /events?tickers=…&with_nested_markets=true`, batched | every market of a matched event that is not fully settled |
| `near` | :15, :30 and :45 runs | matched events only | matched-event markets resolving within 48 h |

A **matched event** has a row in `odds_matches` and at least one market
without a settlement, and resolved no more than 2 days ago. Unmatched
markets get one snapshot a day at most. Every run prints
`matched markets by sport: <sport>=<n>, …`.

The ticker lookup has no status filter, so a finalized matched market comes
back with its result and is recorded in `settlements` on the next run.

**Order:** the odds step (when due) runs before the matched fetch, so a game
matched this run is snapshotted this run. The daily scan runs first on daily
runs, and the odds step matches against games known from the database
(daily scan plus targeted lookups).

**Exchange closed.** Each run first reads `GET /exchange/status`. If
`exchange_active` is false (Kalshi's weekly maintenance is Thursday
03:00–05:00 ET), the run records `exchange_closed`, prints `EXCHANGE CLOSED`,
writes nothing else, exits 0 and does not alert. Observed 2026-10-22 00:20
PDT: `/events?tickers=…` returned 503 throughout the window. The heartbeat
counts closed runs and flags more than 12 in 7 days, since the weekly
window is 8 runs.

---

## Amendment 2026-10-22: lean cleanups (owner request)

The owner's request called these "unprotected". They change `snapshot.py`,
which is on the protected list, so they wait for owner review like the rest.

1. **The daily scan writes price rows only for matched markets.** Matched
   means a ticker in `odds_matches`, excluding events with a match conflict.
   Every scanned market still updates the `markets` table, and finalized
   markets in the scan are still recorded in `settlements`. As a result,
   the digest and heartbeat counts of "games seen in the full scan" now
   count matched games only.
2. **Single-market settlement lookups only for matched markets.** The
   pending query joins `odds_matches`. The bulk settled-market sweep is
   unchanged; it is one paginated call, not per-market lookups.
3. **The shadow step takes `checked_events` from the odds step** (see the
   GAP_v2 amendment).

---

## Amendment 2026-10-22: a daily run prices what it matches (owner-approved fix)

**The problem (observed 2026-10-22).** On a daily run the scan comes first.
It writes price rows only for markets that were already matched. The odds
step then matches new games, but the matched fetch that would price them
runs only on hourly and near runs. So a game matched on a daily run had no
price row until the next run. Examples:
- The first live run (2:15 PM PT) matched 44 markets and wrote 0 price
  rows. The 2:30 PM PT run wrote 44.
- Games matched through targeted lookups on a daily run had no price row
  either.

This broke "a game matched this run is snapshotted this run". On a daily
run where a check was due, the check would have paid for odds on a game
with no Kalshi price to compare.

**The rule.** On a daily run, after the odds step, the matched fetch also
runs (`GET /events?tickers=…`, batched), but only for matched events that
got no price row from the scan in this run.
- Every market of those events is written, as on an hourly run, with tier
  `daily`.
- Events already priced by the scan are not fetched again.
- No odds credits are spent.
- The run summary records the count as `matched_after_scan`.

**Unchanged:** hourly and near runs, the scan itself, settlements and the
shadow step. The shadow step still compares only games checked in that
same run, now with their price from this run.
