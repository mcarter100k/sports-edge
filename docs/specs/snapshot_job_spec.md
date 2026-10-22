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
