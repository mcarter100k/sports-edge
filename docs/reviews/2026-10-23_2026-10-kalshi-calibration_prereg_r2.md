# Review: 2026-10-kalshi-calibration, prereg

- Reviewed commit: `7ab227ca77954a21c9c7460e1fc7ae85e144d873`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T08:31:04Z
- Saved verbatim below; not edited.

---

VERDICT: REVISE

Summary: Revision 2 resolves all 14 findings from the first review. The leakage check now covers every tested price. Weather decision time no longer reads `close_utc`. Timing evidence is given for all five series. The sd is conservative, the family is fixed at five tests, and the null-result wording no longer overclaims. One new major problem comes from the revision itself. The exclusion bound B is applied before any rejection is claimed, but the power section ignores it. So the study does not have the 80% power it states for δ = 0.02: the pooled power to *claim* that effect is about 60%, and the Football secondary test can only claim effects of about 0.05 or more. A few minor differences between the pre-registration and the script remain.

Findings:

1. [severity: major] research/registry/2026-10-kalshi-calibration/prereg.md:181-200 against prereg.md:228-239 and research/studies/kalshi_calibration.py:272-274, 357-360. The power section states a pooled detectable effect of 0.0140 and says δ = 0.02 needs 4,906 games. But section 9 claims a rejection only if the 95% bootstrap interval, widened by B on each side, excludes 0. That widening raises the bar, and section 6 never says so.
   - **Pooled, recomputed:**
     - SE ≈ 0.5/√10,059 = 0.00499, so the unwidened interval half-width is about 0.0098.
     - B ≈ 0.0087 + 0.0122·|Δ̂|.
     - A claim therefore needs |Δ̂| > about 0.0187.
     - At a true Δ of 0.02, power to claim is Φ((0.02 − 0.0187)/0.00499) ≈ 0.60. With the observed RMS sd of 0.469 it is about 0.66. Both figures are before any design effect from clustering.
     - 80% power to claim needs Δ of about 0.023. That is above δ, and about equal to the 0.0225 follow-up threshold.
   - **Football:** the half-width is about 0.0211 and B is about 0.0249 + 0.034·|Δ̂|. A claim needs |Δ̂| > about 0.048, against the stated "detectable 0.0302".
   - **Why it matters:** readers of a null result will believe the study could have found a 0.02 bias. Under its own claim rule it had roughly a coin-flip-plus chance. That changes what a non-claim means.
   - **Fix:** in section 6, state the power to *claim* under the B rule, pooled and per sport, and the effect needed for 80% under that rule. Then either accept the lower power explicitly or change the design. For example, narrow the bound or move the follow-up threshold. Do this before the run.

2. [severity: minor] kalshi_calibration.py:328-329, 368-371 against prereg.md:240-244. The pre-registration fixes the follow-up threshold at 0.0225 (fee 0.0175 plus the pre-run median half-spread of 0.0050). It says the run-time half-spread is only *printed*. The script instead computes the threshold as `FEE_AT_HALF + median(half_spread of scored games)` at run time.
   - The run-time median will probably also be 0.0050, because spreads come in whole cents. But it is computed after rule 7, which reads outcomes.
   - **Fix:** hard-code the threshold at 0.0225 and print the run-time half-spread separately.

3. [severity: minor] kalshi_calibration.py:358-363 and prereg.md:247-249. Two wording problems:
   - **The null result ignores B.** The non-rejection statement says an excess outside [lo, hi] is "ruled out". But the study's own position is that rule 4 could move Δ by up to B. The consistent statement is "outside [lo − B, hi + B]".
   - **The "not claimed" reason can be wrong.** When p < 0.05 but the *unwidened* bootstrap interval already includes 0 (the z test and the percentile bootstrap can disagree), the text still says the claim failed "because the interval widened by B includes 0".
   - **Fix:** widen the ruled-out interval by B, and print the actual reason a claim failed.

4. [severity: minor] prereg.md:105-128. Sports t is `expected_expiration_utc − 4 h`, read from *settled* markets. Nothing directly confirms that this field holds the same value it had before the game. If Kalshi rewrote it at settlement, t would carry game length, which is the same concern raised about `close_utc`, but for every game rather than 168.
   - **Evidence against a rewrite:** close − expected varies widely (NFL p5 −2.9 h, NCAAF p95 +6.9 h). That argues it is not rewritten to the close time. But it is indirect.
   - **Fix:** compare the pre-game expected expiration of the 39 live-checked games, as recorded live, with the frozen value. If they are not in the dataset, say so in section 3.

5. [severity: minor] tests/test_kalshi_calibration.py. Three paths have no end-to-end test:
   - **A nonzero bound in the printed result.** Synthetic game 5 is excluded by the guard and has a price, but no test asserts B in the output.
   - **A group whose units all fall on one date.** It should enter BH with p = 1 and be labelled INSUFFICIENT. That fix (first-review finding 12) is only tested for empty groups.
   - **The "rejected, not claimed" and "Met./Not met." text.**
   - **Fix:** add these tests before the run.

Status of the first review's 14 findings:

| # | Finding | Status |
|---|---|---|
| 1 | Tested prices came from the full connection | **Resolved.** `check_no_peeking(..., sorted(every), sample=None)` (script:286-294) compares every scored, rule-4-excluded and exploratory decision with an isolated prediction and stops on any difference. `priced()` and the bound take p from the full connection, which the check proves identical. Rule 5–6 exclusions are None-versus-value differences, which the check also covers. The text says what the check does not cover (prereg:145-153; script:336-339). The half-spread is read from the full connection without its own check, but it uses the same query and is descriptive only. |
| 2 | Rule 4 reads `close_utc` | **Resolved.** Wording corrected (prereg:130-136). Counts are registered (prereg:76-79). The bound is algebraically valid: the per-game shift is at most 1 − p_dog + \|Δ\| (script:230-236; test:132-139). The claim rule is implemented (script:272-274, 384). Its side effect on power is new finding 1. |
| 3 | Weather t came from `close_utc` | **Resolved.** `wall_time_to_utc(ticker_date, 0, zone)` (script:129; timeutil.py:67-69). `close_utc` is unused for weather, and a test moves close by 3 days to confirm (test:142-152). The zone map is correct for all seven cities. |
| 4 | No MLB or NHL timing evidence | **Resolved.** All five series, with p5 and p95 (prereg:112-118). Residual: new finding 4. |
| 5 | Null-result wording overclaimed | **Resolved, with a residual.** Pooled excess only, and buckets are descriptive (script:362-363). See new finding 3. |
| 6 | Harness approval not recorded | **Resolved as recorded:** docs/validation_log.md:889-892. I cannot verify the owner's approval itself, or that the file is unchanged since 334c293 (NOT CHECKED, no git access). |
| 7 | Rules 5–6 not counted; units unverified | **Resolved.** Counts per sport and source and the price ranges are given (prereg:80-87). The arithmetic is consistent: 10,163 − 88 − 16 = 10,059 = 8,245 + 1,814. |
| 8 | "Latest candle" wording | **Resolved.** Prereg:80-83 and 141-143 match harness.py:76-81 and script:162-171. |
| 9 | sd 0.45 not conservative | **Resolved.** sd 0.50. |
| 10 | Weather metric and sd | **Resolved.** The meaning is stated (prereg:159-164; script:389-391). sd = √(S(1−S))/k is the correct H0 variance of (Σy − S)/k. |
| 11 | Weather clusters split a measured day | **Resolved.** `cluster = day.isoformat()` (script:133; test:152). |
| 12 | Edge cases in the family | **Resolved.** Always five tests, untestable entered as p = 1 (script:324-326; test:155-162). A single-date group gives p = None, which counts as untestable. Not tested end to end (new finding 5). |
| 13 | δ framing | **Resolved, with a residual.** The fee-only δ is stated. The threshold is computed at run time rather than fixed (new finding 2). |
| 14 | Labels | **Resolved.** Script:250 and 406. |

Checklist:

1. **Leakage:** no leakage found in the prices.
   - **Quote filter:** `ts_utc <= ? AND ts_utc >= ?` (harness.py:80). `<=` is correct, because `end_period_utc` is the end of the hour.
   - **Isolation connection:** inserts only rows `<= t` and has no `outcomes` table (harness.py:129-140). Every decision is checked (script:294).
   - **Inputs:** no last price, `price_close`, `settlement_value` or `settlement_utc` is loaded (script:76, 153-158). Outcomes are read only at rule 7 (script:187) and in `harness._rows`.
   - **Decision time:**
     - Sports t depends on `expected_expiration_utc`, which is not directly verified as the pre-game value (finding 4).
     - Rule 4 reads `close_utc`, which settles with the outcome. This is disclosed and bounded.
     - Weather t is set by the calendar. The NWS climate day in daylight time starts at 01:00 local daylight time, so midnight is earlier, which is safe.
   - **Survivorship:**
     - Every game market settles, and voids are excluded by a rule that does not depend on y.
     - Rule 2 holds for all 10,331 games.
     - Flipping outcomes does not change inclusion (test:112-120).
     - The 44 rule-4 exclusions without a tested price would also fail rule 5–6, so leaving them out of B is valid.

2. **Unit of N:** no issue found.
   - One market per game, the smaller ticker (script:93-96). N is reported as games.
   - Weather is collapsed to city-days (script:222-227). Brackets are labelled not independent.
   - Counts are consistent: NFL 476 + NCAAF 1,872 + NBA 1,532 + NHL 1,761 + MLB 4,690 = 10,331. Per sport, the rule-4 exclusions (Football 108, Basketball 9, Hockey 5, Baseball 46) add up to 168.

3. **Clustering:** no issue found.
   - The cluster-robust SE with the G/(G−1) factor is correct (script:203-207).
   - The bootstrap resamples whole sorted clusters with a seed (stats.py:117-133).
   - Sports use the Los Angeles date of t, pooled across sports, so cross-sport correlation on the same date is handled.
   - Weather uses the measured date across all seven cities.
   - Power ignores the design effect; this is acknowledged.

4. **Power:** the arithmetic checks out; the framing does not (finding 1).
   - Recomputed with z₀.₉₇₅ + z₀.₈ = 2.80159:
     - n_for_effect(0.5, 0.02) = ⌈4,905.5⌉ = 4,906 ✓
     - pooled detectable 0.0140 ✓
     - Football 0.0302 ✓, Basketball 0.0359 ✓, Hockey 0.0336 ✓, Baseball 0.0206 ✓
     - Weather 0.0709 × 2.80159 / √2,557 = 0.0039 ✓
   - The known part of the bound is consistent across groups: per-group sums of (1 − p_dog) of about 55.6 + 6.7 + 3.3 + 23.4 ≈ 89, against 0.0087 × 10,183 ≈ 88.6.
   - Both sds come from prices and formulas, not outcomes.
   - N < 20 is labelled INSUFFICIENT (script:324, 378-382).

5. **Multiple testing:** no issue found. One primary test at α = 0.05, plus a fixed five-test BH family (script:326). The exploratory tests compute p values but print only Δ and the interval (script:411-415). The Wilson intervals are labelled "unadjusted, not a test". Harness paired differences are not printed.

6. **Pre-registration integrity:** differences between the script and the pre-registration:
   - the run-time follow-up threshold (finding 2);
   - the unregistered "no expected expiration" exclusion (script:97-99), count 0 by the arithmetic.
   
   The rest matches: the rule order, the fold at p ≤ 0.5, weather p < 0.5, the bucket edges, seed 20261022, 2,000 resamples, α and q. No `result.md` exists in the registry folder. The dataset id `34babdc762e232b0` matches dataset_manifest.json:2. NOT CHECKED:
   - that 20afe15 precedes 7ab227c;
   - that the script is unchanged between them;
   - the manifest sha256;
   - that the committed manifest copy matches the frozen one (reading the frozen folder was denied).

7. **Determinism:** no issue found.
   - Sets are sorted before use (script:294; harness.py:157, 177).
   - Funnels are printed sorted. Group order is fixed by tuples.
   - The bootstrap is seeded with sorted clusters.
   - DuckDB runs single-threaded with ORDER BY on insert.
   - No wall-clock time or temporary path appears in the output, and float formats are fixed.
   - A byte-identity test exists (test:87-92), in one process only.

8. **Reporting honesty:** the null path is now honest, apart from the B residual (finding 3). "Calibration is not profit" and "no result here is an edge" are stated (script:416-421; prereg:41-43). The main honesty gap is the power claim (finding 1).

9. **Other:**
   - docs/specs/harness_spec.md:25 defines the units cluster as "the Los Angeles date of the decision". Weather now uses the measured date. That is harmless, because the harness takes whatever cluster it is given, but the spec should say "as the study defines it".
   - The guard is not airtight for very long games (MLB extra innings, NHL overtime) whose expected expiration was an early-start placeholder. The tight MLB and NHL quantiles (p5 −0.6 h and −0.3 h) make this rare.
