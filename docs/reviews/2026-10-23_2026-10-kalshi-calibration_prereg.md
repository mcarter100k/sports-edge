# Review: 2026-10-kalshi-calibration, prereg

- Reviewed commit: `492fe502de86ae1aca56984482871adc825b717b`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T08:17:56Z
- Saved verbatim below, except one trailing session note about unavailable editor connectors, which the reviewer marked as not part of the review; it was removed.

---

VERDICT: REVISE

Summary: The sports design is mostly sound. It uses one market per game, the unit is the game, standard errors and bootstrap resample Los Angeles dates, and the power arithmetic is correct. But the leakage section claims more than the code does. Three things rest on settled-market timing fields that change when the outcome happens, or that have not been checked: the inclusion rules, the weather decision time, and 63% of the sports sample (MLB and NHL). The registered reading of a null result also claims "calibrated", which the metric cannot show. All of this can be fixed before the run without reading outcomes.

Findings:
1. [severity: major] research/registry/2026-10-kalshi-calibration/prereg.md:104-111 and research/studies/kalshi_calibration.py:132-148, 238-247, 267-269 — The pre-registration says every scored prediction is made on the isolation connection. The primary and secondary test values are not. `priced()` calls `harness.kalshi_quote` on the full connection, which holds outcomes and later quotes (line 137). The p used in every test comes from that call. The isolated predictions from `harness.score()` feed only the descriptive "harness score" section.
   - The numbers are probably identical, because the same SQL runs both times, but nothing asserts it.
   - The quote and spread exclusions (rules 5–6) are decided on the full connection.
   - The exploratory decisions at 5 h and 8 h (lines 243-247) are never leakage-checked.
   - The result text (267-269) would still tell readers the tests were run on isolated data.
   - The harness check only covers quote timestamps against t. It cannot detect a leaky t (findings 2–4), yet the pre-registration offers it as the leakage guarantee.
   - Fix: take each test's p from the isolated predictions, or assert that each `srows` p equals the isolated prediction and stop if not. Run the harness check on the exploratory decisions too. Reword section 3 and the result text to say exactly what the check covers.

2. [severity: major] prereg.md:51-56, 104 and kalshi_calibration.py:81-88 — The pre-registration says "nothing timestamped after t is read, except the outcome". That is false. Rule 4 (the open-period rule and the timing guard) reads `close_utc`, and in settled game markets that is the time play ended.
   - docs/specs/snapshot_job_spec.md:20-25 shows close_time on an open game market is a backstop about 45 h after the game. The pre-registration's own check (lines 79-85) shows the settled close sits near the end of play. So the field is rewritten when the outcome happens.
   - The guard therefore selects on game length, which is tied to the result. The pre-registration admits this (lines 93-96) but does not bound it.
   - 168 games (1.6%) are excluded with this field. At |y − p| ≈ 0.5, that is enough to move Δ by up to about 0.008, which is about two thirds of the detectable effect (0.0125).
   - Fix: correct the wording. Before running, register:
     - the excluded counts by sport and by price bucket at t (no outcomes needed);
     - a worst-case bound on Δ from the excluded games;
     - a rule that a rejection which does not survive the bound is not claimed.

3. [severity: major] prereg.md:66-68, 99-100 and kalshi_calibration.py:102-103 — Weather decision time is t = close_utc − 24 h. No evidence is given that weather close_utc is fixed in advance.
   - The disclosure (200-209) lists no weather timing check, and "about 1 a.m. local" is asserted without a source.
   - Finding 2 shows Kalshi rewrites close_time at settlement on game markets. If weather markets close when their outcome is known, or at varying times, then t is set by information after t.
   - Fix: derive t from the measured date in the event ticker, at a fixed local time. Otherwise, before running, report the distribution of weather close time-of-day per series (timing fields only) and register a tolerance.

4. [severity: major] prereg.md:74-87 — The check of the decision time covers NFL, NCAAF and NBA only.
   - MLB (4,644 games) and NHL (1,756) are 63% of the 10,163 units, and their close − expected spread was never examined.
   - The live check of 39 games (2026-10-22) includes EPL and La Liga, which are out of scope. On that date MLB had at most the World Series, so the live evidence for MLB is about one game.
   - Only medians and 5th percentiles are given. The upper tail (expected expiration set as a placeholder, so t is far from "about an hour before the start") is not given.
   - Fix: add the same timing-only quantiles for MLB and NHL, including the 95th percentile, to the disclosure before running. Re-justify the 3 h guard for those sports if needed.

5. [severity: major] prereg.md:22-23, 193-194 and kalshi_calibration.py:282-283 — The null-result rule says "Kalshi's underdog pricing is calibrated to within the detectable effect." That does not follow from the test.
   - Δ is a mean over all underdog prices. Overpriced longshots and underpriced near-even underdogs can cancel to zero, which is exactly the shape the favorite-longshot bias usually has.
   - The detectable effect is computed from an assumed sd and ignores the design effect from clustering.
   - Fix: on non-rejection, report only that a pooled underdog excess outside the observed cluster-bootstrap 95% interval is ruled out. Describe bucket-level calibration from the reliability tables, and label it descriptive.

6. [severity: major] prereg.md:4-5 — The harness is "held for owner review". `research/harness.py` is PROTECTED (AGENTS.md), and so is the `result.md` this study writes. I found no record of owner approval in docs/validation_log.md. If the study runs before the owner approves the harness at 334c293, it produces a protected result from an unreviewed evidence path.
   - Fix: record the owner's approval of the harness at 334c293 before the run, and cite it in the pre-registration.

7. [severity: minor] prereg.md:57-61 — Rules 5 and 6 are "counted at run time", but they use candles only, with no outcomes. They can and should be counted now, by sport and by `candles.source` (live or historical).
   - research/backfill.py:310-314 reads the historical schema's plain `close` field as if it were in dollars. That assumption is tested only by a fixture (tests/test_research_backfill.py:37-40).
   - If the real historical candles are in cents, `yes_ask < 1` (harness.py:80) silently drops every pre-cutoff market as "no two-sided quote". The stated N and power would then be wrong.
   - Fix: register the rule 5–6 counts, and the min and max of yes_bid_close and yes_ask_close per source.

8. [severity: minor] prereg.md:57-58, 101-103 versus research/harness.py:76-81 — The pre-registration says the mid of "the latest hourly candle" no older than 6 h. The code uses the latest *two-sided* candle, skipping a later one-sided candle and falling back up to 6 h. The spread filter is applied to that fallback quote. Fix: make the pre-registration wording match the code (or the other way round).

9. [severity: minor] prereg.md:136-138 — sd = 0.45 is described as conservative, but it may not be.
   - Folded underdog prices in NBA and MLB cluster at about 0.38–0.50, where √(p(1−p)) is 0.49–0.50.
   - The price distribution comes from candles, with no outcomes, so it can be used legitimately.
   - With the upper bound sd = 0.5: N needed 4,906 (not 3,974); pooled detectable 0.0139; Baseball 0.0206, Football 0.0296, Basketball 0.0359, Hockey 0.0334.
   - Fix: use 0.5, or the sd from the observed price distribution.

10. [severity: minor] prereg.md:117-118, 153-156 and kalshi_calibration.py:177-186 — The weather city-day value is the mean of (y − p) over sub-0.50 brackets. It is mostly driven by whether a ≥0.50 favorite, or an excluded one-sided tail bracket, wins.
    - If every included bracket is below 0.50 and the winner is among them, Σy = 1. The value is then (1 − Σp)/k, which is fixed by prices alone.
    - The test is still valid under H0, but "longshot excess" overstates what it isolates.
    - sd 0.30 is a guess. The formula sd ≈ √(S(1−S))/k (S = summed price of included brackets, k = their count) can be computed from prices.
    - Fix: state this interpretation and derive the weather sd from that formula.

11. [severity: minor] prereg.md:119-121 and kalshi_calibration.py:121, 147 — For weather, clustering on the Los Angeles date of t (about 1 a.m. local) splits one measured day across clusters.
    - 1 a.m. Eastern or Central time falls on the previous Los Angeles date.
    - Los Angeles falls on the same date.
    - Denver lands at about midnight Pacific, on the boundary.
    - Fix: cluster weather on the measured date from the event ticker.

12. [severity: minor] kalshi_calibration.py:255-259 and research/stats.py:176 — Two edge cases:
    - A group with ≤1 unit is dropped from the BH family, so m becomes less than the registered 5.
    - A group whose units all fall on one date gives p = NaN (lines 161-162). `benjamini_hochberg` then raises, and the run crashes instead of reporting.
    - Fix: keep m = 5 as registered, and put a non-testable group in the family with p = 1 (and label it INSUFFICIENT).

13. [severity: minor] prereg.md:139-141, 188-191 — δ = 0.02 is justified by the taker fee with multiplier 1 (0.07 × 0.25).
    - config/fee_schedule.json does not exist in this checkout, so the sports multipliers are unconfirmed (sports_edge/fees.py:5-7, 124-131).
    - The taker's cost also includes half the spread, which can be up to 5 cents under rule 6.
    - Fix: state δ as "fee only, multiplier assumed 1", or raise it to fee plus a typical half-spread for the "|Δ| ≥ 0.02" follow-up rule.

14. [severity: minor] prereg.md:166-170 — The reliability tables print a Wilson interval for each bucket: about 40 sports buckets and 12 weather buckets, none adjusted. In the weather table, "n" is brackets, not city-days. Fix: label the intervals "unadjusted, not tests" and the weather n as "brackets (not independent)".

Checklist:
1. Leakage — Issues found (findings 1–4).
   - Quote filter: `ts_utc <= t AND ts_utc >= t − 6 h` (harness.py:78-81). `<=` is correct, because `end_period_utc` is the end of the candle's hour.
   - Isolation connection: inserts rows `<= t` only and has no outcomes table (harness.py:129-140).
   - Inputs: no last price, settlement or result fields. `price_close` is not loaded (kalshi_calibration.py:123-124). Outcomes are read only for rule 7 and for scoring (lines 134, 144).
   - Problems: the inclusion rules read `close_utc`, which is rewritten after the event (finding 2). Weather t comes from `close_utc` (finding 3). MLB and NHL timing is unverified (finding 4). The test values are computed on the full connection (finding 1).
   - "Settled markets" survivorship: benign for games. Every game market settles, voids are excluded by a registered rule, and the void exclusion does not depend on y. tests/test_kalshi_calibration.py:111-119 confirms inclusion does not change when outcomes are flipped.
2. Unit of N — No issue found.
   - Sports: one market per two-market event, the smaller ticker (lines 73-76). N is reported as games. The units table keys on event (line 121). The other market is not used.
   - Weather: collapsed to city-day (lines 177-186).
   - Row counts agree: 2 × 10,331 + 15,372 = 36,034 = markets rows (dataset_manifest.json:25).
3. Clustering — Mostly fine.
   - Cluster-robust SE with G/(G−1) is computed correctly (lines 157-161).
   - The bootstrap resamples whole clusters in sorted order with a seed (stats.py:117-133).
   - Pooling sports on one date in one cluster handles cross-sport correlation on the same date.
   - The weather cluster is misdated (finding 11). Power ignores the design effect; this is acknowledged at line 146.
4. Power — Recomputed with z₀.₉₇₅ + z₀.₈ = 2.8016:
   - n_for_effect(0.45, 0.02) = ⌈3973.5⌉ = 3,974 ✓.
   - Detectable effect: at 10,163, 0.0125 ✓; Football 0.0266 ✓, Basketball 0.0323 ✓, Hockey 0.0301 ✓, Baseball 0.0185 ✓; Weather (sd 0.30, n 2,562) 0.0166 ✓.
   - The sd comes from a formula, not outcomes, but may be optimistic (finding 9). Weather sd is unjustified (finding 10).
   - INSUFFICIENT for N < 20 is implemented (lines 199, 294, 297) and tested (test line 106).
5. Multiple testing — The registered family is complete for the tests the script runs: 1 primary plus 5 secondary, BH q = 0.05 (lines 258-259). The exploratory cluster tests compute p values but print only Δ and the interval (lines 318-320), which is acceptable. Edge cases: findings 12 and 14.
6. Pre-registration integrity — Script against pre-registration, differences:
   - "Latest candle" versus "latest two-sided candle" (finding 8).
   - The leakage claim versus where the tests are computed (finding 1).
   - An extra "no expected expiration" exclusion (line 77), counted, with count 0 as implied by 10,331 − 59 − 109 = 10,163.

   Everything else matches: the rules in order, fold at p ≤ 0.5, the bucket edges, 2,000 bootstrap resamples, seed 20261022, α and q. No result.md exists in the registry folder. The dataset id 34babdc762e232b0 matches dataset_manifest.json:2.

   NOT CHECKED:
   - the manifest sha256 `db0b97…` (no hashing tool available);
   - the git order of 492fe50 and 334c293 (git metadata is outside the worktree and read access was denied). I relied on the caller's statement.
7. Determinism — No issue found. I checked:
   - every set is sorted before iteration (harness.py:157, 177, 283; kalshi_calibration.py:71 and 120);
   - dict output is sorted or in a fixed order (lines 271-274, 291);
   - the bootstrap is seeded with sorted clusters;
   - DuckDB uses one thread, inserts with ORDER BY, and candles are unique per (ticker, end_period_utc) (backfill.py:426);
   - no wall-clock time or temporary path appears in the output;
   - floats are printed with fixed formats.

   A byte-identity test exists (tests/test_kalshi_calibration.py:87-92). It runs in a single process and does not vary PYTHONHASHSEED. I found no hash-order dependence.
8. Reporting honesty — The negative-result path exists, and "no edge" and "calibration is not profit" are stated (lines 321-326). The null-result wording overclaims (finding 5). The δ and cost framing is incomplete (finding 13).
9. Other — The harness is unreviewed (finding 6). The historical candle price units are unverified (finding 7).
