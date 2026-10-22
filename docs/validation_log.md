# Validation log

Append-only. A correction is a new dated entry; earlier entries are never
rewritten. Statuses: VERIFIED, OBSERVED, INFERRED, PENDING, NOT CHECKED.

## PENDING LIVE CONFIRMATION

Entries are cleared with a date and the evidence that reality produced.

- **Daily digest at 7:30 AM Pacific** (added 2026-10-22): the first scheduled
  notification after `install.sh` loads `com.quis.sportsedge.digest` at merge.
  Until then the wrapper has run only under `scripts/reality_run.py`.

---

## 2026-10-21 — Phase 1 specs: source facts

- VERIFIED (live call): `GET https://external-api.kalshi.com/trade-api/v2/series?category=Sports`
  with no credentials returned HTTP 200 and 4,029 series. Fee pairs:
  quadratic/1 = 3,904, quadratic_with_maker_fees/1 = 106, quadratic/0.5 = 18,
  quadratic_with_maker_fees/0.5 = 1. No `flat`.
- OBSERVED (live scan): 8,325 open sports events, 93,241 open sports
  markets, 16,385 resolving within 48 h (by the earlier of expected
  expiration and close time). 2,449 events have competition_scope `Game`.
- OBSERVED: `GET /markets?min_close_ts=…&max_close_ts=…` across all
  categories returned HTTP 504. The design does not depend on it.
- NOT READ: the Kalshi fee schedule PDF (HTTP 429 to every automated
  fetch). The 0.07 taker coefficient is pinned by the docs' fee-rounding
  worked example. See `docs/specs/fee_model_spec.md` open items.

## 2026-10-22: GAP_v2 amended in place before any live run

- Change: the book last-update age is no longer a gate. It remains a
  record-only field (`book_update_age_s`) on every row. The only staleness
  gate is the reference fetch age ≤ 15 min. The `book_line_stale` skip is
  removed.
- Why amended in place: GAP_v2 has written no live rows (no live ledger
  exists; launchd is not installed), so no evidence carries the earlier
  definition. Owner-directed.
- Evidence for this change: see the run report of 2026-10-22 (suite result
  and the mutation test that re-adds the gate).

## 2026-10-22: daily digest report (Part A) built; scheduling not built

- **Built:** `sports_edge/digest.py` (not on the protected list), per
  `docs/specs/daily_digest_spec.md` Part A and its amendment. **Not built:**
  the 7:30 AM launchd plist and wrapper (Part B). Those are protected and
  wait for owner review. The request's premise ("not a protected-file
  change") holds for Part A only.
- **Gate 1:** 11 digest tests, plus 1 fee test pinning that
  `quadratic_with_combo_maker_fees` fails closed against a table without a
  maker fee.
- **Mutation checks:** 13 digest mutants and 1 fee mutant, each killed and
  each reverted byte-identical.
  - The first window-edge mutant survived. Shifting both edges swapped
    which boundary row counted, so the total did not change. The test was
    made asymmetric (two games at the start edge, one at the end) and all
    three edge variants are now killed.
  - A test fixture used `fee_unknown` where the run summary writes
    `skip_fee_unknown`; it was corrected before the mutant that adds fee
    skips to "Skipped" was run.
- **Gate 2 (reality, `scripts/reality_run.py`):**
  - A real read-only snapshot found 8,343 games, 93,288 markets and 22
    categories, with 0 live files changed and no failure markers.
  - The digest at the real time printed "no snapshot run finished
    yesterday", which is true: the only run was today.
  - With the clock moved forward 24 hours, the first version showed two
    definition faults. Both were corrected in the spec amendment.
  - Final output on real data: every zero names its cause (no matched
    games, no fee table, grader never run, nothing checked, odds not
    configured). A second run the same day wrote `_2` and left the first
    report unchanged.
- **NOT CHECKED in reality:** the populated money, closing-price and
  bets-spotted lines. They need an odds key, a fee table and graded rows,
  none of which exist yet. Only Gate 1 covers them.

## 2026-10-22: digest banner and 7:30 AM schedule built (owner-approved Part B); not installed

- **Owner rulings applied:**
  - Banner format with `notify.sh` unchanged.
  - Part B approved, to be installed with the other jobs at merge.
  - The August 20 combo fee date is news reporting, UNCONFIRMED until the
    owner checks kalshi.com/fee-schedule. Recorded as a correction in
    `fee_model_spec.md`, and in the Phase 3 spec, in commits made before
    this code.
- **Built:**
  - The banner in `sports_edge/digest.py`. Line 1 of its output is the
    banner, held to 190 characters and enforced.
  - `scripts/run_digest.sh`: always notifies, is loud on any failure, and
    exits 1.
  - `scripts/launchd/com.quis.sportsedge.digest.plist`, firing at 7:30.
  - The new label in `install.sh` and `uninstall.sh`.
- **Gate 1:**
  - 5 banner tests, including the worst-case length plus notify's suffix
    within the 220-character fallback cut.
  - 8 wrapper tests under `/bin/bash` 3.2.
  - 3 new launchd configuration tests.
  - **14 mutants each killed and reverted byte-identical:** 7 in the
    wrapper, 3 in the plist and installer, 4 in the banner. The config test
    first missed a plist pointing at the wrong wrapper (it checked only that
    the script existed); it was tightened before the mutation run.
  - **Suite:** 215 passed, 1 skipped.
- **Gate 2 (reality):** the real wrapper under `/bin/bash`, on the real
  snapshot data, through `reality_run.py`:
  - **Without `.env`:** exit 1 and a loud alert, "DIGEST FAILED | exit 1:
    .env missing …".
  - **With the test-only `SPORTS_EDGE_NO_ENV_FILE`:** exit 0, posting
    "Sports-edge: no games scored yet · system problems →
    digest_2026-10-22_2.txt".
  - 0 live files changed.
- **Not installed** (owner ruling): `install.sh` was not run.
- **PENDING:** the first 7:30 AM firing (top of this log).
- **NOT CHECKED in reality:** a banner with real numbers. It needs odds, a
  fee table and graded bets.

## 2026-10-22 02:01–02:09 PDT: step 1 under standing approval (batch probe and reality gate)

- VERIFIED (live): `GET /events?tickers=…&with_nested_markets=true` returned
  every requested event for batches of 10, 50, 100 and 200 in one request each
  (0.4–0.6 s). A batch of 400 also returned everything, in 2 requests (the
  client followed the 200-per-page cursor). Events that contain finalized
  markets come back with no status filter. The default batch stays 50.
- VERIFIED (reality_run, scratch folder, live data hashes unchanged): the daily
  cycle exited 0. Tier daily, 8,243 sports events, 93,043 markets, 93,043
  snapshot rows, 22 sports, 1,709 settlements from the scan, 173 Kalshi
  requests, 2 min 56 s.
- VERIFIED: the settlement sweep records when there is something to record.
  The daily run's sweep added 0. A direct check found 216 swept markets known
  to us, 204 already recorded, and the rest settled at or after 09:04:05Z,
  after that sweep ran. The grader's sweep then added 20, plus 3 single
  lookups.
- VERIFIED: the hourly cycle with no matches made 1 Kalshi request (exchange
  status only), did no full scan, wrote 0 snapshot rows, and printed
  "matched events: 0 (no odds matches yet)".
- OBSERVED: the heartbeat module reported NEEDS ATTENTION (exit 1), naming
  odds not configured, fee table missing, 0 matched sports, and "never
  succeeded". The last is because health state is written by the wrapper
  scripts, which were not used in this run, while job_runs shows 2 ok runs.
- PENDING: the measured odds cycle (waiting on the key), the fee table
  (waiting on the PDF), the matched-only fetch with real matches (needs the
  key), and everything time-dependent once launchd is installed.

## 2026-10-22: slate and pre-game alerts, spec committed before code

- **Spec:** `docs/specs/slate_alerts_spec.md`, plus amendments to the
  shadow ledger, odds matching, snapshot, storage, fee and grader specs.
- **GAP_v2 is amended in place** (owner direction). It has written no live
  row: no `data/shadow_ledger.csv` exists, and launchd is not installed.
  The changes:
  - stake $10, so contracts = floor(10 ÷ ask);
  - depth must cover that size, walking the book;
  - a gap ≥ 3¢ after the taker fee;
  - rows only at a check.

  The ledger moves to schema_version 3, because columns were added.
- **VERIFIED (live, 2026-10-22, no credentials):**
  `GET /markets/KXNBAGAME-26OCT22CLEPHI-CLE/orderbook?depth=5` returned HTTP
  200 with `orderbook_fp.yes_dollars` and `no_dollars` bid levels. The best
  No bid (0.58 × 570,333.51) equals the market's `yes_ask_size_fp` at Yes
  ask 0.42.
- **OBSERVED (machine, 2026-10-22):**
  - **Sleep:** after 1 minute on battery, never on AC. It was running on
    battery at the time.
  - **Scheduling a wake:** `sudo -n` asks for a password, so `pmset
    schedule` cannot run unattended.
  - **Replies:** `~/Library/Messages/chat.db` returns "authorization
    denied" without Full Disk Access.
  - **OS:** macOS 13.0.
