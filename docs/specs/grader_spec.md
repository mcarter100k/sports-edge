# Spec: grader (`sports_edge/grader.py`, `scripts/run_grader.sh`)

Status: Phase 1. Protected files. Runs nightly at 03:30 America/Los_Angeles
under launchd.

## Steps

1. **Settlement sweep:** the same sweep as the snapshot job's step 6.
2. **Calibration (Kalshi vs reference):** for each **finalized** market with
   result `yes` or `no` that has a match and has not been graded yet:
   - `p_kalshi` = the mid `(yes_bid + yes_ask) / 2` from the **last** snapshot
     before `commence_utc` that has both sides quoted;
   - `p_ref` = the fair probability from the **last** reference fetch before
     `commence_utc`;
   - `y` = 1 if the result is `yes`, else 0;
   - **Brier** = (p − y)². **Log loss** = −[y ln p + (1−y) ln(1−p)], with p
     clipped to [1e-6, 1 − 1e-6]. Computed for both p's.
   - The market is skipped (and counted under a named reason) if either p is
     missing. A market graded on only one side would bias the comparison.
   - One row per market goes to `data/calibration_grades.csv`
     (append-only, idempotent by `market_ticker`): ticker, event, sport,
     y, p_kalshi, p_ref, kalshi snapshot time, ref fetch time, brier
     and log loss for each, graded_utc.
3. **Shadow P&L:** for each shadow row whose market is finalized and not yet
   graded, per contract:
   `pnl = payout − price_paid − fee_per_contract`, where payout = 1 if the
   side bought won, else 0. Settlement is assumed fee-free (open item in
   `fee_model_spec.md`). One row per shadow row goes to
   `data/shadow_grades.csv` (append-only, idempotent by `row_id`).
   A result other than yes/no (void, scalar) is excluded and counted.
4. **Report** (printed, and in the day log):
   - calibration: N markets, **N events** (the outcomes of one game are not
     independent), mean Brier and mean log loss for each source, and the
     difference;
   - shadow: rows graded; **first-fire** rows (the first row per
     market + side + rule_id, which is the unit for any claim); distinct
     events; mean and median P&L per contract on first-fire; and the mean
     without the best event;
   - any count of independent events below 20 is labelled **INSUFFICIENT**.
     Nothing is ever called an edge in Phase 1.

## Failure

Exit 1 on any exception. Zero settled-and-matched markets is **not** a
failure. It is reported as `0 graded (no matched market has settled yet)`,
together with the counts of matched markets and settled markets, so that
"none yet" can be told apart from "path broken".

---

## Amendment 2026-10-22 (owner review findings 3 and 4)

**Calibration** also scores `p_ref_power`: the power-method fair probability
from the same last pre-start fetch. Brier and log loss are reported for
Kalshi, reference (proportional) and reference (power).

**CLV per shadow row**, written in `shadow_grades.csv`, record-only:
- `closing_fair_prob`: the proportional fair probability of the row's
  `ref_outcome`, at the last reference fetch before `commence_utc`;
  `closing_ref_fetched_utc` is the time of that fetch;
- `closing_side_fair` = closing fair for YES, and 1 − closing fair for NO;
- `clv` = `closing_side_fair − price_paid` (in probability points, before
  fees);
- `fair_move` = `closing_side_fair − the side's fair probability when the row
  was logged`.

These fields are blank if there was no reference fetch before the start.
The report gives mean and median first-fire CLV next to P&L.

---

## Amendment 2026-10-22: taken and not-taken bets (see `slate_alerts_spec.md`)

The report adds two lines, in plain words. The bet log is
`data/bet_log.csv`, and the latest reply per pick is the one used.

- **Bets you took:**
  - **Counts:** bets and games.
  - **Settled result in dollars:** at the price taken and
    floor($10 ÷ price) contracts, after the taker fee, using the row's
    multiplier and precision.
  - **Open:** a count of bets not yet settled.
- **Picks not taken:** the same figures for every other first-fire pick,
  at the logged price and the logged contract count.

Both lines are always printed. With no bet log, they read "no replies
logged yet". These lines never decide anything; they are a record.

---

## Amendment 2026-10-22: the closing line must be a later read, and it counts the fee (owner review)

- **Closing reference.** It is the last reference fetch for the row's game
  and `ref_outcome` that is:
  - **strictly after** the row's `snapshot_utc`;
  - **strictly after** the row's own reference fetch (`odds_fetched_utc`);
  - **before** `commence_utc`.

  **Why both:** on a daily run, the Kalshi snapshot comes from the scan
  that runs before the odds fetch. "After the snapshot" alone would let the
  entry fetch serve as its own close.
- **If no such fetch exists,** every closing field is blank. That covers a
  game read once (a single pull), which on the free plan is every game.
- **`clv` = `closing_side_fair − price_paid − fee_per_contract`.** This means
  beating the close after fees. A 1¢ price beat can be negative once the fee
  is taken off.
- **Unchanged:** `fair_move`, the report line (it now names the fee), and
  calibration, which still uses the last reference before the start.
