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
- **Test statistic** for mean D, M or R: z = (mean − null) / cluster-robust
  standard error, with clusters = Los Angeles dates, and SE² = G/(G−1) ×
  Σ_clusters (Σ (x − mean))² / N².
  - **Null values:** −0.01 for D (non-inferiority), 0 for M and R.
  - **One-sided:** a promotion needs z above the boundary.

## 3. Checkpoints and false positives from repeated looks

**When:** the tests are evaluated only when the count of qualifying games
first reaches **100, 250, 500 and 1,000**. Between checkpoints the stage
line repeats the last checkpoint's verdict and shows the running count.

**The problem.** Testing the same accumulating data four times at the usual
z = 1.96 gives a 7.3% chance that a no-edge strategy passes at some look,
not 2.5% (simulated below).

**The rule: alpha spending for promotions, kills at any look.**

1. **Promotions use O'Brien–Fleming-type boundaries** (Lan–DeMets
   spending, α(t) = 2 − 2Φ(2.2414/√t), overall one-sided α = 0.025 per
   test). Information fraction t = n / 1,000. The boundaries were computed
   by recursive numerical integration over the four looks:

   | Look (games) | t | α spent by then | Promotion boundary z |
   |---|---|---|---|
   | 100 | 0.10 | 1.4 × 10⁻¹² | **7.00** |
   | 250 | 0.25 | 7.4 × 10⁻⁶ | **4.34** |
   | 500 | 0.50 | 0.0015 | **2.96** |
   | 1,000 | 1.00 | 0.025 | **1.97** |

   - Both Early-signal tests (D and M) use these boundaries.
   - The Confirmed returns test is made at one look only (1,000 games),
     with z = 1.96.

2. **Kills (demotion to Practice) at every look**, at the nominal z ≤
   −1.96:
   - M is significantly negative: Kalshi moved away from the side bought;
   - or D is significantly below −0.01: Pinnacle moved toward Kalshi's
     price.

   **Kills are non-binding.** The promotion boundaries are computed as if
   no kill existed, so a kill can only lower the chance of a false
   promotion, never raise it. An early look therefore cannot promote
   unless the evidence is overwhelming, but it can always stop a strategy
   that is failing.

**Overall false-positive rate across all four looks** (simulated, 200,000
paths under the null, one test):

| Rule | P(any look passes, no edge) |
|---|---|
| z = 1.96 at every look (no correction) | 0.073 |
| z = 2.50 at every look (Bonferroni, the first draft of this spec) | 0.020 |
| **O'Brien–Fleming spending (this rule)** | **0.025** (exactly α by construction; simulated 0.0248) |
| One look at 1,000 only, z = 1.96 (reference) | 0.025 |

**For the stages:**
- **Early signal** needs both the D and M tests to pass at the same look.
  Under either null (no convergence, or adverse drift) the test that should
  fail has at most 0.025 chance of passing over all looks. So
  **P(false Early signal) ≤ 0.025.**
- **Supported** needs Early signal, so P(false Supported) ≤ 0.025.
- **Confirmed** needs Early signal and the one-look returns test, so
  P(false Confirmed) ≤ 0.025.

**The cost of kills to a real edge** (simulated, z ≤ −1.96 at any look):
- 7.3% when the true mean equals the null exactly, which is the right
  outcome there;
- 1.0% when the true mean is 0.05 sd per game above the null;
- 0.2% at 0.1 sd per game.

## 4. Tiers

| Stage | Earliest look | Needs, at that look and every later one |
|---|---|---|
| Practice | — | below 100 qualifying games, no promotion yet, or a kill |
| **Early signal** | 100 games (in practice 250, see section 5) | **Drift not adverse:** z_D = (mean D + 0.01) / SE above the look's boundary (non-inferiority, margin 1 cent, a third of the 3-cent threshold). **And Kalshi converges:** z_M = mean M / SE above the look's boundary. |
| **Supported** | 500 games | Early signal holds, and the mean R > 0. This is a point estimate, used as confirmation, not a test. |
| **Confirmed** | 1,000 games | Early signal holds, and z_R = mean R / SE ≥ 1.96 |

**Once promoted,** a stage stays unless a kill fires at a later look, or the
v1 20-bet losing stretch fires (unchanged).

The line names the reason, for example
`Practice (STAGE_v2: Pinnacle moved toward Kalshi at 250 games)`.

## 5. Power and timing (stated before any data)

**Assumptions.** The standard deviations below are assumptions, not
measurements. No live row exists. Each is to be replaced by the sd
observed in the first 50 qualifying games. That sd is recorded in
`docs/validation_log.md` before the 100-game look.
- **sd of D:** 0.03 (a Pinnacle moneyline moving 0 to 3 cents between the
  60- and 15-minute reads).
- **sd of M:** 0.03 to 0.05.
- **sd of R:** 1.0 (a bought binary returns about −100% or +100%).

**Effect size** is (true mean − null) / sd per game:
- D, true mean 0, sd 0.03: (0 + 0.01) / 0.03 = 0.33;
- M = 0.01 with sd 0.03: 0.33; with sd 0.05: 0.20.

**Probability that a test has promoted by each look** (cumulative,
simulated with these boundaries):

| Effect per game | by 100 | by 250 | by 500 | by 1,000 |
|---|---|---|---|---|
| 0.05 | 0.00 | 0.00 | 0.03 | 0.35 |
| 0.10 | 0.00 | 0.00 | 0.23 | 0.88 |
| 0.15 | 0.00 | 0.03 | 0.65 | 1.00 |
| 0.20 | 0.00 | 0.12 | 0.93 | 1.00 |
| 0.33 | 0.00 | 0.82 | 1.00 | 1.00 |

**Returns:**
- Supported at 500 passes 50% of the time with no edge, and 75% with a
  true 3% return. It is confirmation, not a test.
- Confirmed (z ≥ 1.96 at 1,000) detects only a return of about 8.9% or more
  at 80%. A true 3% return needs about 8,700 games for 80% power.

**In plain terms:**
- 100 games are never enough to promote. The 100-game look exists to kill.
- If the gaps behave like stale Kalshi prices (Pinnacle steady, Kalshi
  moving toward it by about a cent), Early signal arrives at 250 games
  with probability about 0.8, and by 500 almost surely.
- Returns cannot confirm a small edge on this horizon. Confirmed is a high
  bar on purpose. Reaching it would mean a large edge, and failing it does
  not disprove a small one.

## 6. Free plan

The free plan reads each game once, so there is no closing reference read
and D cannot exist. Under `ODDS_PLAN=free` the line stays at Practice and
says `STAGE_v2 needs two reference reads per game (paid plan)`, as v1 does
today.

## 7. Tests the implementation must have

- **Pinned checkpoint values:** synthetic graded rows at 99, 100, 249 and
  250 games. The verdict changes only at a checkpoint.
- **Pinned boundaries:** 7.00, 4.34, 2.96 and 1.97. A z of 4.33 at 250 does
  not promote, and 4.35 does.
- **Kills:** z_M = −1.97 at the 100-game look demotes even after an earlier
  promotion; z_M = −1.95 does not.
- **Boundaries reproduced from the spending function** in a test, not only
  stored as constants.
- **Each condition alone:** a demotion when D is adverse while M > 0, and
  when M ≤ 0 while D is fine.
- **Unit counting:** two bets on one game count as one unit.
- **The margin:** mean D = −0.009 passes, −0.011 fails, each with a tight
  interval.
- **Supported and Confirmed** gate on returns exactly as in the table.
- **A positive control for the v1 defect:** rows whose entry gap is 0.03
  and whose D is −0.02 pass v1's beat-the-close test but fail v2.
