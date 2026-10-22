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
