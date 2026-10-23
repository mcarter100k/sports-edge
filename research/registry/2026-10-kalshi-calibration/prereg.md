# Pre-registration: Kalshi calibration by price bucket and sport (favorite-longshot check)

Study `2026-10-kalshi-calibration`, **revision 4** (2026-10-23).
- **Script:** `research/studies/kalshi_calibration.py` at commit `586ca79`.
- **Harness:** `research/harness.py` (PROTECTED). The owner approved it at
  commit `334c293` on 2026-10-22, and it is unchanged since (`git diff
  334c293 -- research/harness.py` is empty). The approval is recorded in
  `docs/validation_log.md`.

**Revision 4** answers the third review
(`docs/reviews/2026-10-23_2026-10-kalshi-calibration_prereg_r3.md`, verdict
APPROVE, 5 minor findings):
1. The result now prints the registered claim-power figures next to the
   plain detectable effect.
2. Section 6 states when the follow-up triggers, and the design-effect
   caveat for the claim table.
3. Section 3 adds the MLB off-grid evidence and the sports in the live
   sample.
4. Rule 4 lists the zero-count "no expected expiration" exclusion.
5. The validation log is updated.

Script changes since revision 3 (`98f8b3a` → `586ca79`): the printed power
lines and the follow-up caveat only. No rule, threshold or computation
changed.

**Revision 3** answered the second review
(`docs/reviews/2026-10-23_2026-10-kalshi-calibration_prereg_r2.md`, verdict
REVISE: all 14 first-round findings resolved, 1 new major and 4 minor):

| Finding (second review) | Change |
|---|---|
| 1. The power section ignored the exclusion-bound claim rule | Section 6 now states the power to *claim* under the B rule, pooled and per sport, and the effect needed for 80%. The lower power is accepted explicitly: the pooled test reaches 80% claim power at about Δ = 0.023, about the 0.0225 follow-up threshold. |
| 2. The follow-up threshold was computed at run time | Fixed in the script at 0.0225. The run-time half-spread is printed separately for reference. |
| 3. The null interval ignored B; the "not claimed" reason could be wrong | A null now rules out only an excess outside [lo − B, hi + B]. A failed claim prints its actual reason: the bound, or the bootstrap interval disagreeing with the z test. |
| 4. Expected expiration might be rewritten at settlement | Evidence added in section 3, from timing fields only. |
| 5. Three paths without end-to-end tests | Tests added: the bound printed; a single-date group entered as INSUFFICIENT with p = 1; every claim and verdict path. Two more: a leak on an excluded decision stops the run, and the tested price applies the spread rule. |

**Revision 2** answered the first pre-registration review
(`docs/reviews/2026-10-23_2026-10-kalshi-calibration_prereg.md`, verdict
REVISE). The study has not run on this dataset; no outcome has been read.

| Finding | Change |
|---|---|
| 1. Tested prices came from the full connection | The tested price is `FilteredMid`. Every decision (scored, rule-4-excluded and exploratory) is checked with the harness: the full-data prediction must equal the prediction on a separate connection holding only quotes at or before t, with no outcomes. Section 3 now says what that check covers and what it does not. |
| 2. Rule 4 reads `close_utc`, which settles with the outcome | The wording is corrected (section 3). The excluded counts and a worst-case exclusion bound B are registered, and a rejection is claimed only if it survives B (sections 2, 9). |
| 3. Weather t came from `close_utc` | Weather t is now local midnight at the start of the measured date, taken from the event ticker. `close_utc` is not read for weather. |
| 4. No MLB or NHL timing evidence | Quantiles for all five series, including the 95th percentile, are in section 3. |
| 5. The null-result wording overclaimed | A null result now reports only the pooled excess ruled out. Bucket-level calibration is descriptive. |
| 6. Harness approval not recorded | Recorded (above, and in the validation log). |
| 7. Rules 5–6 not counted; units unverified | Counted per sport and source (section 2). Both candle sources are in dollars: bids 0.00–0.99, asks 0.01–1.00. |
| 8. "Latest candle" wording | Now "latest **two-sided** candle"; the spread rule applies to that candle. |
| 9. sd 0.45 not conservative | sd 0.50, the upper bound, is used. The observed underdog prices give an RMS √(p(1−p)) of 0.469. |
| 10. Weather metric and sd | The metric's meaning is stated, and the sd is derived from prices: 0.0709. |
| 11. Weather clusters split a measured day | Weather now clusters on the measured date. |
| 12. Edge cases in the family | The family is always five tests; an untestable group enters with p = 1, labelled INSUFFICIENT. |
| 13. δ framing | δ is fee-only with the multiplier assumed 1. The follow-up threshold is the fee plus the median half-spread: 0.0225. |
| 14. Labels | The Wilson intervals are labelled "unadjusted, not a test". The weather n is "brackets (not independent)". |

## 1. Question and hypothesis

- **Question:** At a fixed time before the event, do underdogs in settled
  Kalshi sports game markets win as often as their mid price says, pooled
  across games?
- **H0:** Δ = 0, where Δ = mean over games of (y − p) on the underdog side.
- **H1:** Δ ≠ 0 (two-sided). The favorite-longshot bias predicts Δ < 0, but
  Kalshi's participants could just as well overprice favorites.
- **Why it matters for trading:** a claimed bias larger than a taker's
  costs is a candidate rule that needs no outside odds and no sport model.
  It would go to a prospective, fee-adjusted shadow test on data after this
  dataset. Calibration is not profit, and this study claims no edge either
  way.

## 2. Data

- **Dataset:** `kalshi-backfill`, id `34babdc762e232b0`, `manifest.json`
  sha256
  `db0b979fdffa3b58f1af055fd7f40d5197bd6eae6363d2161dcd01dc432d852c`. A copy
  is in `dataset_manifest.json`. Tables: markets 36,034, candles 2,684,145,
  outcomes 36,034.
- **Source:** Kalshi public market data only (`research/backfill.py`, scope
  fixed in `docs/specs/research_spec.md` before any outcome was read).
  - Sports: settled markets of KXNFLGAME, KXNCAAFGAME, KXNBAGAME,
    KXNHLGAME and KXMLBGAME, from April 2025.
  - Weather: KXHIGHNY, KXHIGHCHI, KXHIGHMIA, KXHIGHAUS, KXHIGHLAX,
    KXHIGHDEN and KXHIGHPHIL, closing on or after 2025-10-22.
- **Fields:**
  - `markets`: ticker, event_ticker, series_ticker, grp, market_type,
    open_utc, close_utc, expected_expiration_utc;
  - `candles`: ticker, end_period_utc, yes_bid_close, yes_ask_close
    (hourly);
  - `outcomes`: ticker, result, read only for rule 7 and for scoring.
- **Sport groups:** Football (NFL and NCAAF), Basketball (NBA), Hockey
  (NHL), Baseball (MLB). Weather is its own group.

**Sports inclusion, in order** (counts computed before registering, without
outcomes):
1. All games (events) in the sport groups: 10,331.
2. Exactly two markets, both binary: 10,331.
3. **The market used:** the one with the smaller ticker. This depends on
   neither price nor outcome.
4. **Decision time** t = expected_expiration_utc − 4 h, inside
   [open_utc, close_utc), and the **timing guard** close_utc − t ≥ 3 h
   (3.5 h for football).
   - **Games left:** 10,163 (59 outside the open period, 109 by the guard).
   - **A game with no expected expiration** is also excluded. Its count is
     0 in this dataset (10,331 − 59 − 109 = 10,163).
   - **Of the 168 excluded,** 124 have a tested price at t: Football 76,
     Basketball 9, Hockey 4, Baseball 35. These enter the exclusion bound
     (section 9).
5. **A two-sided quote:** the latest candle with 0 < bid ≤ ask < 1 and
   t − 6 h ≤ end_period_utc ≤ t. One-sided candles are skipped. Excluded:
   88 (Football 82, Baseball 6).
6. **Spread:** ask − bid ≤ 0.10, on that same candle. Excluded: 16 (Hockey
   13, Baseball 3).
   - **Eligible after rules 5–6:** 10,059 (Football 2,158, Basketball
     1,523, Hockey 1,743, Baseball 4,635).
   - **Both candle sources contribute:** historical 8,245 and live 1,814.
7. **Result yes or no:** voids and other results are excluded. Counted at
   run time; this is the only rule that reads outcomes.

**Weather inclusion:**
1. Every bracket market in scope: 15,372, in 2,562 city-days.
2. **Decision time** t = 00:00 local time (the city's zone) on the measured
   date in the event ticker (`KXHIGHNY-26OCT21` → 2026-10-21 00:00
   America/New_York). The market must be listed by t (open_utc ≤ t).
   Excluded: 30 brackets not yet listed. Left: 15,342. `close_utc` is not
   read.
3. **Rules 5–6 per bracket:** no two-sided quote 810, spread 214, eligible
   14,318.
4. **Rule 7,** per bracket, at run time.
5. **Brackets priced below 0.50 at t:** 2,557 city-days have at least one.

## 3. Decision time and what the leakage check covers

**Sports t.** Kalshi gives no start time.
- **Live check:** on 39 matched live games (2026-10-22), expected
  expiration minus the Pinnacle start was 160–180 min. MLB was 1 of the 39
  games, and EPL and La Liga (1 each) are outside this study.
- **Historical check** (timing fields only), close_utc − expected_expiration_utc
  per game, in hours:

| Series | Games | p5 | p25 | median | p75 | p95 | share closing > 1 h early | share closing > 6 h late |
|---|---|---|---|---|---|---|---|---|
| NFL | 476 | −2.9 | −0.0 | +0.2 | +0.3 | +0.9 | 8.2% | 0.8% |
| NCAAF | 1,872 | −0.3 | +0.3 | +0.6 | +1.2 | +6.9 | 1.9% | 7.7% |
| NBA | 1,532 | −0.5 | −0.4 | −0.2 | −0.1 | +0.2 | 0.6% | 1.0% |
| NHL | 1,761 | −0.3 | +0.0 | +0.2 | +0.4 | +0.9 | 0.3% | 0.5% |
| MLB | 4,690 | −0.6 | −0.3 | −0.1 | +0.2 | +1.0 | 0.8% | 1.6% |

**What the table shows:**
- In settled markets the expected expiration sits near the actual end of
  play, so t is about 1 h before the start.
- **A market closing late** (the expected time was an early placeholder, as
  seen live for Saturday college games) puts t *earlier* than intended,
  which is safe.
- **A market closing early** could put t inside the game. The timing guard
  removes those cases. The MLB and NHL distributions are as tight as NBA's,
  so the 3 h guard needs no change.

**Rule 4 reads `close_utc`, which settles when play ends.** This is the
one way information after t enters the selection, and it is reported, not
hidden:
- it excludes 168 games;
- game length is weakly tied to the result (an MLB home win skips the
  bottom of the ninth);
- the exclusion bound (section 9) limits what those games could do to Δ.

**Weather t** is fixed by the calendar (the measured date's local
midnight). The weather rules read no field that settles with the outcome.

**Expected expiration is not rewritten at settlement** (second review,
finding 4):
- **Live versus frozen:** 34 markets matched live on 2026-10-22, whose
  `expected_expiration_time` the live database recorded before they
  settled, are in the frozen dataset with the identical value (34 of 34).
  Their expected expiration minus the Pinnacle scheduled start is 160–179
  min.
- **Scheduled times, not settlement times:** across the frozen settled
  markets, expected expiration falls on a whole 5-minute mark, like a
  schedule, for 100% of NBA, NCAAF, NFL and NHL markets and 93.0% of MLB.
  Actual close times do so for 0.3–1.7%, which is chance for clock times
  to the second. The field carries scheduled times, not the time play
  ended.
- **MLB, the weakest case** (46% of the sample): the 7.0% of MLB markets
  (328 games) whose expected expiration is off the 5-minute grid fall on
  minutes ending in 2 or 3. That fits first pitches at odd minutes plus a
  fixed offset of about 2 h 55 min (7:07 + 2:55 = 10:02). Their close − expected (median −0.06 h, p5 −0.54, p95 +0.69) matches
  the other MLB games. Only 6.7% sit within 2 minutes of the close, whereas
  a rewrite to the end of play would put nearly all there.
- **The live sample of 34** is NHL 22, NCAAF 6, NBA 4, NFL 2 and MLB 0.
  MLB rests on the grid evidence alone.
- **The limit:** the dataset holds no snapshot taken before each game, so
  this evidence is indirect. A value updated between listing and the
  game, for example after a postponement, is the one used.

**The price at t** is the mid of the latest **two-sided** hourly candle with
t − 6 h ≤ end_period_utc ≤ t. A candle's `end_period_utc` is the end of its
hour, so all of its data are known at t.

**What the leakage check covers (registered run, no sampling):**
- **The comparison:** every tested price is compared with the same
  prediction made on a separate connection holding only quotes at or
  before its t, with no outcomes table. That covers every scored
  decision, every rule-4-excluded decision used in the bound, and every
  exploratory decision. Any difference stops the run.
- **What it proves:** the prices used at t did not use later data.
- **What it cannot check:** whether t itself was chosen with later
  information. That is the job of the timing rules above.

## 4. Unit of N and independence

- **Sports:** the unit is the game, one market per game (rule 3).
- **Weather:** the unit is the city-day. The value is the mean of (y − p)
  over its brackets priced under 0.50.
  - **What that value measures:** if the winning bracket is one of them,
    Σy = 1 and the value is (1 − Σp)/k, set by prices alone. So the test
    mainly asks whether the sub-0.50 brackets, taken together, win as
    often as their summed price says. It does not isolate single
    longshots.
- **Clustering:**
  - Sports cluster on the Los Angeles calendar date of t.
  - Weather clusters on the measured date, so one measured day is never
    split.

## 5. Primary metric and test

- **Fold to the underdog side:** if p ≤ 0.5 keep (p, y), otherwise use
  (1 − p, 1 − y).
- **Δ** = mean over games of (y_dog − p_dog), pooled across the four sport
  groups.
- **Test:** a z test with a cluster-robust standard error (clusters =
  dates, factor G/(G−1)), two-sided, **α = 0.05**.
- **Also reported:** a 95% cluster-bootstrap interval (2,000 resamples,
  seed 20261022).

## 6. Power

- **sd of (y − p) = 0.50,** the upper bound of √(p(1−p)). The observed
  underdog prices at t (no outcomes) give an RMS √(p(1−p)) of 0.469, with
  quartiles 0.325, 0.405 and 0.455.
- **Smallest effect worth detecting:** δ = 0.02. That is the taker fee at
  p = 0.5 with the multiplier assumed 1 (0.0175), rounded up.
- **N needed** at 80% power, α = 0.05: `n_for_effect(0.50, 0.02) = 4,906`
  games.
- **N eligible before rule 7: 10,059.** The detectable effect is 0.0140,
  before any design effect from clustering.
- **Per sport** (secondary), detectable at 80%:
  - Football 0.0302 (n 2,158);
  - Basketball 0.0359 (n 1,523);
  - Hockey 0.0336 (n 1,743);
  - Baseball 0.0206 (n 4,635).
- **Power to *claim* under the exclusion-bound rule (section 9).** A claim
  needs the 95% interval, widened by B on each side, to exclude 0. That is
  a higher bar than the test alone. With SE = sd/√n, B = b0 + c·|Δ| (b0
  as in section 9, c = n excluded with a price ÷ (n + that)), z = 1.96:

| Group | smallest claimable \|Δ̂\| | power to claim at Δ = 0.02 | Δ for 80% claim power |
|---|---|---|---|
| Pooled | 0.0187 | 0.60 (0.66 at the observed sd 0.469) | 0.0229 |
| Football | 0.0476 | 0.01 | 0.0570 |
| Basketball | 0.0297 | 0.23 | 0.0405 |
| Hockey | 0.0254 | 0.33 | 0.0355 |
| Baseball | 0.0195 | 0.52 | 0.0258 |

  **Accepted as registered:**
  - The pooled study can claim, with about 80% power, a bias as large as
    the 0.0225 follow-up threshold, which is the size that would matter
    for trading.
  - It cannot reliably claim a 0.02 bias, and the per-sport tests are much
    weaker. Football in particular can claim only biases of about 0.05 or
    more, because its rule-4 exclusions are large.
  - A non-claim is read with these numbers. The result prints them.
  - The table is before any design effect from clustering.
  - **The follow-up needs both a claim and |Δ̂| ≥ 0.0225.** It therefore
    triggers with 80% probability only at a true Δ of about 0.0225 +
    0.84 × 0.0050 ≈ 0.0267. At a true Δ of 0.0225 it triggers about half
    the time.
- **Weather:** sd 0.0709. It is derived from prices as the RMS over
  city-days of √(S(1−S))/k (S = summed price of the sub-0.50 brackets,
  capped at 1; k = their count). At n 2,557 the detectable effect is
  0.0039.

## 7. Secondary and other analyses

- **The secondary family (section 8):** Δ per sport group, and the
  weather value.
- **Descriptive only, not tests:**
  - reliability tables and Brier decompositions (sports underdog side,
    pooled and per sport, buckets of 0.05 up to 0.50; weather every
    bracket, buckets 0, .05, .1, .2, …, .9, .95, 1). The Wilson intervals
    are unadjusted and labelled as not tests, and the weather counts are
    brackets, not independent units.
  - the harness score of the tested price (Brier, log loss). The Pinnacle
    baseline will read "not available": the dataset has no reference
    prices.
- **Exploratory, not tested:** pooled sports Δ with t at 5 h and 8 h before
  expected expiration, with the same guard.

## 8. Multiple testing

- **Primary:** one test, α = 0.05.
- **Secondary family:** exactly five tests (Football, Basketball, Hockey,
  Baseball, Weather), Benjamini–Hochberg at q = 0.05. A group that is not
  testable (N < 20, or a single date) enters with p = 1 and is labelled
  INSUFFICIENT, so m stays 5.

## 9. Decision rules

- **The exclusion bound** for each sports group and pooled:
  B = Σ over rule-4-excluded games with a tested price of (1 − p_dog +
  |Δ|), divided by (n scored + n excluded with a price). This is the
  largest shift in Δ if those games had been included with the worst
  possible outcomes.
  - The part known now (before the |Δ| term) is: pooled 0.0087;
    Football 0.0249; Basketball 0.0044; Hockey 0.0019; Baseball 0.0050.
  - Weather has no outcome-dependent exclusion, so B = 0.
- **Claimed rejection:** the test's p < 0.05 (primary) or BH-rejected
  (secondary), **and** the 95% cluster-bootstrap interval, widened by B on
  each side, excludes 0. A rejection that fails is reported as "rejected,
  not claimed", with its reason: either the widened interval includes 0, or
  the bootstrap interval itself includes 0 (the percentile bootstrap and
  the z test can disagree).
- **If claimed with |Δ| ≥ 0.0225** (the fee at p = 0.5 with multiplier 1,
  0.0175, plus the median half-spread of eligible games at t, 0.0050):
  propose a prospective, fee-adjusted shadow study on games after this
  dataset's last date, with its own pre-registration. No live action. The
  threshold 0.0225 is fixed in the script; the run-time half-spread of the
  scored games is printed for reference only.
- **If claimed with |Δ| < 0.0225:** a clear but untradeable
  miscalibration. Record it.
- **If not rejected:** report only that a pooled underdog excess outside
  the observed 95% interval, widened by B on each side, is ruled out. Calibration bucket by bucket is
  described from the tables, not concluded.
- **INSUFFICIENT:** any group with fewer than 20 units (AGENTS.md rule 9).

## 10. Disclosure

**What was looked at before registering:**
- Kalshi market listings: field names, series counts and event counts
  (2026-10-22).
- One candle response's shape.
- Open, close and expected-expiration times of 39 live matched games,
  against Pinnacle start times.
- The backfill's per-series counts of markets and of markets with candles.
- Close minus expected expiration for all five series (section 3), read
  2026-10-22 (NFL, NCAAF, NBA) and 2026-10-23 (all five).
- **Expected expiration** of the 34 live-matched, now-settled markets
  against their frozen values, and the share of expected-expiration and
  close times on 5-minute marks (2026-10-23, timing fields only).
- **Candle-only counts and distributions** (2026-10-23):
  - rules 5–6 per sport and source;
  - bid and ask ranges per source;
  - underdog price quartiles and the median half-spread;
  - prices of the rule-4-excluded games;
  - the weather city-day sd.

  They were computed on a connection with no outcomes table.

**Outcomes:** one settled market's `result` field was printed during an API
probe on 2026-10-22 (KXNBAGAME-26OCT22CLEPHI, one game). No other outcome,
and no aggregate of outcomes, has been read.

**Code version:** `586ca79`. **Determinism:** the output depends only on
the dataset files. A test checks byte-identical output for the same
dataset id.
