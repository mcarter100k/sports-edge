# Spec: stage rule v2 (drift and markout first, returns as confirmation)

Status: **PROPOSED, held for owner review.** No code yet. Implementing it
changes `stage()` in `sports_edge/grader.py`, which is PROTECTED. It
replaces the stage-line section of `docs/specs/slate_alerts_spec.md`
("Stage line (fixed in writing before any data)").

**The signal rule is unchanged:** GAP_v2, no new `rule_id`, and no ledger
row is relabelled. What changes is only how graded rows are judged. Every
stage line names its rule (`STAGE_v2`), so the two can never be mixed up.
No live shadow row exists yet, so v1 has judged nothing.

## 1. Why v1 must change

v1 promotes to Early signal when the 95% lower bound of mean
**beat-the-close** is above 0, where

    clv = closing side fair − price paid − fee
        = (entry side fair − price paid − fee) + (closing side fair − entry side fair)
        = entry gap + fair_move.

GAP_v2 logs a row only when the entry gap is at least 0.03. So the mean CLV
is at least 0.03 plus the mean `fair_move`, and the test passes unless
Pinnacle moves an average of more than 3 cents against the side bought.
**The test is close to automatic.** It checks the bound the rule already
imposes, not whether the gap was real.

**What separates a real gap from a bad one** is how the two prices move
after the row:
- **Kalshi stale, Pinnacle right (an edge):** Pinnacle stays roughly where
  it was (`fair_move` ≈ 0), and Kalshi's price moves toward it
  (`markout_close` > 0).
- **Kalshi informed, Pinnacle late (adverse selection):** Pinnacle moves
  toward Kalshi's price (`fair_move` < 0), and Kalshi does not come back
  (`markout_close` ≤ 0).

Both fields are already recorded for every graded row:
- `fair_move` is in `shadow_grades.csv`. It is closing side fair minus entry
  side fair, from Pinnacle. Negative means Pinnacle moved toward Kalshi's
  price.
- `markout_close` is in `shadow_markouts.csv`. It is Kalshi's side mid at
  the last stored price before the start, minus the side mid at entry.
  Positive means Kalshi moved toward the side bought.

v1 also evaluates after every graded game. Repeated looks at a running test
inflate the false-positive rate well past 5%.

## 2. Definitions

- **Bets:** the grader's first-fire rows, as in v1 (`bets.first_fire_ids`).
- **Unit:** a game (`event_ticker`). Per game:
  - **D** = the mean `fair_move` over its bets;
  - **M** = the mean `markout_close` over its bets;
  - **R** = Σ profit per contract ÷ Σ (price + fee) over its bets.

  Only games with both a closing reference read and a closing Kalshi price
  count. A game missing either is counted and shown, never imputed.
- **Lower bound** of mean D, M or R: mean − z × cluster-robust standard
  error, with clusters = Los Angeles dates, and SE² = G/(G−1) ×
  Σ_clusters (Σ (x − mean))² / N². This is analytic, so a one-sided tail of
  0.625% needs no bootstrap.

## 3. Checkpoints (no continuous monitoring)

**When:** the tests are evaluated only when the count of qualifying games
first reaches **100, 250, 500 and 1,000**. Between checkpoints the stage
line repeats the last checkpoint's verdict and shows the running count.

**Error control:** the overall one-sided α of 0.025 is split equally across
the 4 looks (Bonferroni), so each test at each look uses one-sided α =
0.00625, **z = 2.50**.

## 4. Tiers

| Stage | First reached at | Needs, at that checkpoint and every later one |
|---|---|---|
| Practice | — | below 100 qualifying games, or a condition below fails |
| **Early signal** | 100 games | **Drift not adverse:** lower bound of mean D > −0.01 (non-inferiority, margin 1 cent, a third of the 3-cent threshold). **And Kalshi converges:** lower bound of mean M > 0. |
| **Supported** | 500 games | Early signal holds, and the mean R > 0 (a point estimate, as confirmation) |
| **Confirmed** | 1,000 games | Early signal holds, and the lower bound of mean R > 0 |

**Demotion to Practice:**
- At any checkpoint, either Early-signal condition fails.
- The v1 20-bet losing stretch, unchanged.

The line names the reason, for example
`Practice (STAGE_v2: Pinnacle moved toward Kalshi at 250 games)`.

## 5. Power at each tier (stated before any data)

**Assumptions.** The standard deviations below are assumptions, not
measurements. No live row exists. Each is to be replaced by the sd
observed in the first 50 qualifying games. That sd is recorded in
`docs/validation_log.md` before the 100-game checkpoint is evaluated.
- **sd of D:** 0.03 (a Pinnacle moneyline moving 0 to 3 cents between the
  60- and 15-minute reads). Also shown at 0.02 and 0.04.
- **sd of M:** 0.03 to 0.05.
- **sd of R:** 1.0 (a bought binary returns about −100% or +100%).

All figures use z = 2.50.

| Test | n | Power (or detectable effect) |
|---|---|---|
| D not adverse, true mean 0, sd 0.03 | 100 | 0.80 |
| same | 250 | 0.997 |
| same, sd 0.04 | 100 / 250 | 0.50 / 0.93 |
| same, sd 0.02 | 100 | 0.99 |
| M > 0, sd 0.03 | 100 | detects M ≥ 0.010 at 80% |
| M > 0, sd 0.05 | 100 / 250 | detects M ≥ 0.017 / 0.011 at 80% |
| R mean > 0 (Supported) | 500 | passes 50% of the time with no edge; 75% with a true 3% return |
| R lower bound > 0 (Confirmed) | 1,000 | detects only a return of about 10.6% or more at 80%; power at a true 3% return is 0.06 |

**In plain terms:**
- The first two tests can say within 100 to 250 games whether the gaps
  behave like stale Kalshi prices.
- Returns cannot confirm a small edge on any horizon in this table. At z =
  2.50, a true 3% return needs about 12,400 games for 80% power (about
  8,700 at a single look with z = 1.96). Confirmed is a high bar on
  purpose. Reaching it would mean a large edge, and failing it does not
  disprove a small one.

## 6. Free plan

The free plan reads each game once, so there is no closing reference read
and D cannot exist. Under `ODDS_PLAN=free` the line stays at Practice and
says `STAGE_v2 needs two reference reads per game (paid plan)`, as v1 does
today.

## 7. Tests the implementation must have

- **Pinned checkpoint values:** synthetic graded rows at 99, 100, 249 and
  250 games. The verdict changes only at a checkpoint.
- **Each condition alone:** a demotion when D is adverse while M > 0, and
  when M ≤ 0 while D is fine.
- **Unit counting:** two bets on one game count as one unit.
- **The margin:** mean D = −0.009 passes, −0.011 fails, each with a tight
  interval.
- **Supported and Confirmed** gate on returns exactly as in the table.
- **A positive control for the v1 defect:** rows whose entry gap is 0.03
  and whose D is −0.02 pass v1's beat-the-close test but fail v2.
