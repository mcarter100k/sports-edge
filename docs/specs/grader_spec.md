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

---

## Amendment 2026-10-22: markouts (record-only, fixed before any data)

**What it measures.** How the Kalshi price of the side bought moved after a
shadow row was logged. It answers whether the market tended to move toward
the row's side afterwards. It is a record. It gates nothing, and nothing
reads `data/shadow_markouts.csv` except the markout report.

**Why 10–20 minutes and not 5.** Kalshi prices are stored every 15 minutes,
so a price 5 minutes after a row does not exist. The owner chose the next
stored price instead (2026-10-22).

### Definitions

- **Two-sided snapshot:** a `price_snapshots` row of the row's market with
  0 < `yes_bid` ≤ `yes_ask` < 1. This is the same test the ledger's
  `kalshi_mid` uses. Other snapshots are never used.
- **Yes mid** = (`yes_bid` + `yes_ask`) ÷ 2.
- **Side mid:** the Yes mid for a YES row, and 1 − the Yes mid for a NO row.
  Before any fee.
- **Entry mid:** the row's `kalshi_mid` turned into the side mid the same
  way. If `kalshi_mid` is blank, every markout of the row is blank.
- **Start:** the game's latest listed start (`checks.latest_starts`), or the
  row's `commence_utc` when none is listed.
- **Postponed:** the latest listed start is 60 minutes or more after the
  row's `commence_utc` (`bets.POSTPONED_AFTER`). Postponed rows are skipped
  and counted. They are never written.
- **Row time:** the row's `snapshot_utc`.
- **Next:** the first two-sided snapshot with
  row time + 10 min ≤ time ≤ row time + 20 min, and time < start.
- **Close:** the last two-sided snapshot with row time < time < start.
- **markout_next** = side mid at next − entry mid.
- **markout_close** = side mid at close − entry mid.
- A missing next or close leaves that markout blank.

### When a row is measured

- A row is measured once the start has passed (now ≥ start). No settlement
  is needed. By then every price before the start is stored, so the values
  cannot change later.
- Every ledger row is measured, not only first-fire rows.
- **Idempotent by `row_id`:** a row already in `shadow_markouts.csv` is
  never measured or written again.

### The file

`data/shadow_markouts.csv`, append-only, written only through
`ledger.append_rows`. Columns: `row_id`, `market_ticker`, `event_ticker`,
`side`, `rule_id`, `snapshot_utc`, `start_utc`, `price_paid`, `entry_mid`,
`next_utc`, `next_mid`, `markout_next`, `close_utc`, `close_mid`,
`markout_close`, `graded_utc`. `price_paid` is recorded alongside for
comparison. The markouts themselves are measured from the entry mid, not
from the price paid.

### Order in the grader run

Markouts run after the shadow P&L step (step 3). The shadow P&L, the
calibration, `shadow_grades.csv`, the stage line and the bets lines are
unchanged and never read the markout file.

### Report

- **Unit:** the result (`bets.result_key`: in a two-team game with no Tie,
  "Yes on A" and "No on B" are one result), counted in games
  (`event_ticker`). For each result, its earliest first-fire row (by
  `snapshot_utc`, then `row_id`) is used. Each game's value is the mean over
  its results. The report gives the mean over games and the number of
  games, for next and for close separately, only over values that are not
  blank.
- Any number of games below 20 is labelled **INSUFFICIENT**.
- **Wording:**
  `markouts (Kalshi mid of the side bought, before fee, minus the mid at entry; record only): next price 10-20 min later +0.0042 over 12 games (INSUFFICIENT: under 20) · last price before start +0.0090 over 14 games (INSUFFICIENT: under 20)`
- **A zero names its cause** (AGENTS.md rule 8), as
  `markouts: 0 measured (<cause>)`. The first that applies:
  1. `no shadow rows logged yet`;
  2. `no logged game has started yet`;
  3. `no later two-sided price before any start`.

  Postponed rows skipped are counted on the same line when there are any.

---

## Amendment 2026-10-22: settling the owner's own bets (record-only; see `my_bets_spec.md`)

- **Every run** settles the logged bets in `data/my_bets.csv` from the
  `settlements` table, by each leg's recorded Kalshi market. The outcomes
  are lost, won and void leg, as in the spec.
- **Results** go once per bet to `data/my_bet_grades.csv`, through
  `ledger.append_rows`.
- **The report** adds one line: your own bets logged, settled (won, lost,
  void), net dollars, open, and can't settle.
- **Kept separate:** these never touch the shadow ledger, the shadow
  grades, the markouts or the stage line.
