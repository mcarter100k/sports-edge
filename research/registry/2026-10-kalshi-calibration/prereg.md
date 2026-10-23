# Pre-registration: Kalshi calibration by price bucket and sport (favorite-longshot check)

Study `2026-10-kalshi-calibration`. Script: `research/studies/kalshi_calibration.py`
at commit `334c293`. Harness: `research/harness.py` (PROTECTED, held
for owner review) at the same commit.

## 1. Question and hypothesis

- **Question:** At a fixed time before the event, are Kalshi's mid prices on
  settled sports game markets calibrated? In particular, do underdogs win
  as often as their price says?
- **H0:** Δ = 0, where Δ = mean over games of (y − p) on the underdog side.
- **H1:** Δ ≠ 0 (two-sided). The favorite-longshot bias, found in many
  betting markets, predicts Δ < 0: underdogs overpriced, favorites
  underpriced. The test is two-sided because Kalshi's participants could
  just as well overprice favorites.
- **Why it matters for trading:**
  - A bias larger than the taker's costs is a candidate rule (back
    favorites or fade longshots) that needs no outside odds and no
    sport-specific model. It would go to a prospective, fee-adjusted shadow
    test on data after this dataset.
  - If H0 is not rejected, Kalshi's game prices are calibrated to within
    the detectable effect, and calibration-based rules move down the list.
  - Calibration is not profit. This study claims no edge either way.

## 2. Data

- **Dataset:** `kalshi-backfill`, id `34babdc762e232b0`, `manifest.json` sha256
  `db0b979fdffa3b58f1af055fd7f40d5197bd6eae6363d2161dcd01dc432d852c`. A copy is in `dataset_manifest.json` next to this
  file.
- **Source:** Kalshi public market data only (`research/backfill.py`, scope
  fixed in `docs/specs/research_spec.md` before any outcome was read).
  - Sports: settled markets of KXNFLGAME, KXNCAAFGAME, KXNBAGAME,
    KXNHLGAME and KXMLBGAME, from April 2025.
  - Weather: KXHIGHNY, KXHIGHCHI, KXHIGHMIA, KXHIGHAUS, KXHIGHLAX,
    KXHIGHDEN and KXHIGHPHIL, closing on or after 2025-10-22.
- **Tables and fields:**
  - `markets`: ticker, event_ticker, grp, market_type, open_utc, close_utc,
    expected_expiration_utc;
  - `candles`: ticker, end_period_utc, yes_bid_close, yes_ask_close
    (hourly);
  - `outcomes`: ticker, result, read only when scoring.
- **Sport groups:** Football (NFL and NCAAF), Basketball (NBA), Hockey
  (NHL), Baseball (MLB). Weather is reported as its own group.

**Sports inclusion, in order** (counts from the markets table only):
1. All games (events) in the sport groups: 10331.
2. Exactly two markets, both binary: 10331.
3. **The market used:** the one with the smaller ticker. This choice
   depends on neither price nor outcome.
4. **Decision time** t = expected_expiration_utc − 4 h, inside
   [open_utc, close_utc), and **the timing guard:** close_utc − t ≥ 3 h
   (3.5 h for football). A game market closes when the winner is known,
   so a market closing sooner than that after t may mark a game already
   under way at t. Games left: 10163 (59 excluded for a decision time
   outside the open period, 109 by the timing guard).
5. A two-sided quote (0 < bid ≤ ask < 1) from the latest hourly candle
   with t − 6 h ≤ end_period_utc ≤ t. Counted at run time.
6. Spread (ask − bid) ≤ 0.10. Counted at run time.
7. Result yes or no (voids and other results excluded). Counted at run
   time.

**Weather inclusion:**
1. Every bracket market of the 7 series in scope: 15372, in
   2562 city-days.
2. Decision time t = close_utc − 24 h, inside [open_utc, close_utc). This is
   about 1 a.m. local on the measured day: a forecast, before the day's
   temperatures are observed.
3. Rules 5–7 above, per bracket.
4. Brackets priced below 0.50 at t. City-days with none are dropped.

## 3. Decision time and leakage

- **Sports.** Kalshi gives no start time. On the 39 matched live games
  (2026-10-22), `expected_expiration_utc` minus the Pinnacle start was 160
  to 180 minutes in every sport (NBA 160–170, NHL 170, NFL, NCAAF, MLB,
  EPL and La Liga 175–180). So t = expected − 4 h is about 60 to 80 minutes
  before the start.
  - **Historical check (timing fields only, no results).** In the backfill
    listings, close_utc minus expected_expiration_utc per game:
    - NFL: median +0.2 h, 5th percentile −2.9 h; 8.2% closed more than
      1 h early (476 games);
    - NCAAF: median +0.6 h, 5th percentile −0.3 h; 1.9% (1,872 games);
    - NBA: median −0.2 h, 5th percentile −0.5 h; 0.6% (1,532 games).

    So in settled markets the expected expiration sits near the actual end
    of play, and t is about an hour before the start.
  - **Live counter-example (2026-10-22).** Saturday college games listed
    with a placeholder expected expiration (2026-10-24 03:00Z) about 14 h
    *before* the real kickoff. A placeholder of that kind puts t earlier
    than intended, which is safe. One set *later* than the start would put
    t inside the game. **The timing guard (rule 4) removes those cases.**
  - **Possible bias from the guard:** game length is weakly tied to the
    result. For example, an MLB home win skips the bottom of the ninth. The
    guard bites only when a market closes within 3 h of t, and its count is
    reported.
  - **The exploratory offsets** of 5 h and 8 h (section 7) move t further
    before the start, never closer.
- **Weather.** t = close − 24 h. Close is about 1 a.m. local on the day
  after the measured day.
- **The price used:** the mid of the latest hourly candle whose period
  ended at or before t, no older than 6 h. A candle's `end_period_utc` is
  the end of its hour, so all of its data are known at t.
- **Leakage rule:** nothing timestamped after t is read, except the outcome
  for scoring. This is a registered run.
  - **Where predictions are made:** every scored prediction is made on a
    separate connection holding only quote and reference rows at or before
    its t, with no outcomes table.
  - **The check:** every scored decision, with no sampling, is also compared
    with a prediction made on the full data. Any difference stops the run.
  - The script refuses to report if the check fails.

## 4. Unit of N and independence

- **Sports:** the unit is the game, with one market per game (rule 3). The
  same game's other market is its complement and is not used.
- **Weather:** the unit is the city-day. The value is the mean over its
  sub-0.50 brackets.
- **Clustering:** intervals and standard errors cluster on the Los Angeles
  calendar date of t, because games and weather on one date can be
  correlated.

## 5. Primary metric and test

- **Fold to the underdog side:** if p ≤ 0.5, keep (p, y); otherwise use
  (1 − p, 1 − y). The NO side's mid of the same market is exactly 1 − p.
- **Δ** = mean over games of (y_dog − p_dog), pooled across the four sport
  groups.
- **Test:** a z test with a cluster-robust standard error (clusters =
  dates, small-sample factor G/(G−1)), two-sided, **α = 0.05**.
- **Also reported:** a 95% cluster-bootstrap interval (2,000 resamples,
  seed 20261022).

## 6. Power

- **Assumed sd of (y − p):** √(p(1 − p)), averaged over p uniform on
  [0.1, 0.5], is about 0.44. **sd = 0.45 is used.** This comes from the
  formula, not from this dataset's outcomes.
- **Smallest effect worth detecting:** δ = 0.02 (2 cents). That is about
  the taker fee near p = 0.5 (0.07 × 0.25 = 1.75 cents). A smaller
  miscalibration cannot pay for a taker's costs.
- **N needed** at 80% power, α = 0.05:
  `n_for_effect(0.45, 0.02) = 3,974` games.
- **N available** after inclusion rules 1–4: 10163 games. Rules
  5–7 remove more at run time. The effect detectable at 10163 is
  0.0125, before those removals and before any design effect from
  clustering.
- **Per sport (secondary),** detectable at 80%:
  - Football 0.0266 (n 2240);
  - Basketball 0.0323 (n 1523);
  - Hockey 0.0301 (n 1756);
  - Baseball 0.0185 (n 4644).
- **Weather:** sd assumed 0.30, n 2562 city-days, detectable
  0.0166. The sd is conservative: one bracket at p = 0.3 has sd
  0.46, and averaging several brackets a day, whose outcomes are
  negatively correlated, lowers it.
- **If the N scored is below 3,974,** a null result rules out only effects
  larger than the reported detectable effect.

## 7. Secondary analyses

- **Δ per sport group** (Football, Basketball, Hockey, Baseball) and the
  **weather longshot excess.** These five are the secondary family
  (section 8).
- **Descriptive only, no tests:**
  - reliability tables (Wilson 95% intervals) and Brier decompositions:
    pooled sports and each sport on the underdog side, with buckets of
    0.05 up to 0.50; weather on every bracket, with buckets 0, .05, .1, .2,
    …, .9, .95, 1;
  - the harness score of the Kalshi mid (Brier, log loss) for sports and
    weather. The Pinnacle baseline is expected to read "not available":
    the dataset has no reference prices.
- **Exploratory, not tested:** pooled sports Δ with decision offsets of 5 h
  and 8 h before expected expiration, with the same timing guard.

## 8. Multiple testing

- **Primary:** one test, α = 0.05.
- **Secondary family:** five tests (four sports and weather),
  Benjamini–Hochberg at q = 0.05.
- **Exploratory and descriptive** results are not tests and are labelled as
  such.

## 9. Decision rules

- **Reject H0 if** the primary p < 0.05. Report the direction and size of
  Δ.
- **If rejected with |Δ| ≥ 0.02:** propose a prospective shadow study of a
  fee-adjusted rule on games after this dataset's last date. It needs its
  own pre-registration. No live action.
- **If rejected with |Δ| < 0.02:** a statistically clear but untradeable
  miscalibration. Record it and move on.
- **If not rejected:** Kalshi's underdog pricing is calibrated to within
  the detectable effect. Record it as a negative result with the same care.
- **INSUFFICIENT:** any group with fewer than 20 units is labelled
  INSUFFICIENT and not interpreted (AGENTS.md rule 9).

## 10. Disclosure

**What was looked at before registering:**
- Kalshi market listings: field names, series counts and event counts
  (2026-10-22).
- One candle response's shape.
- Each market's open, close and expected expiration times for 39 live
  matched games, against Pinnacle start times.
- The backfill's per-series counts of markets and of markets with candles.
- Close minus expected expiration per game for NFL, NCAAF and NBA
  (timing fields only; quantiles in section 3), 2026-10-22, read before
  the timing guard was added to the script.

**Outcomes:** one settled market's `result` field was printed during an
API probe on 2026-10-22 (KXNBAGAME-26OCT22CLEPHI, one game). No other
outcome, and no aggregate of outcomes, was read before this file was
committed.

**Code version:** `334c293`. **Determinism:** the script's output
depends only on the dataset files. A test checks that two runs on the same
dataset id are byte-identical.
