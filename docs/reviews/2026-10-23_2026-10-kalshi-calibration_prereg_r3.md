# Review: 2026-10-kalshi-calibration, prereg

- Reviewed commit: `be613028ac42de7bbc984c999e008f83d4a24257`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T08:44:47Z
- Saved verbatim below, except one trailing session note about unavailable editor connectors, which the reviewer marked as not part of the review; it was removed.

---

I've checked all five second-review findings against revision 3, the script and the tests, and confirmed that the first review's 14 resolutions still hold. I'm writing the review now.

VERDICT: APPROVE

Summary: Revision 3 fixes all five second-review findings, and the first review's 14 fixes still hold in the code at `98f8b3a`. I recomputed the power arithmetic, the inclusion counts and the bound, and they check out. I found no leakage, unit, clustering or multiple-testing problem. I found no blocking or major problem. Five minor wording and disclosure gaps remain. Two of them are in text the script will write into `result.md`, which cannot be edited after the run, so fix those first. I could not verify the git order of the commits or the frozen manifest's hash (no access). The owner should check both before running.

Findings:

1. [severity: minor] research/studies/kalshi_calibration.py:387-389 against prereg.md:226-246. The script writes a different power figure from the one section 6 says a reader should use.
   - **What the script writes:** "the smallest effect detectable at 80% power is 0.0140" (z test only).
   - **What section 6 says:** a non-claim must be read with the claim-power numbers (smallest claimable |Δ̂| 0.0187; 80% claim power at Δ ≈ 0.0229). `result.md` will never show them.
   - **Why it matters:** this is the same misreading the second review's major finding 1 was raised to prevent, and the result is the document the owner will read.
   - **Fix:** fix the registered claim-power figures in the script as constants, as was done for `FOLLOWUP` (line 47), and print them next to the 0.0140. Do this before the run.

2. [severity: minor] prereg.md:239-242 against kalshi_calibration.py:301-303. The text says the pooled study can claim, with about 80% power, a bias as large as the 0.0225 follow-up threshold. That is true for the claim: power is Φ((0.0225 − 0.0187)/0.00499) ≈ 0.78. But the follow-up also needs the point estimate |Δ̂| ≥ 0.0225.
   - **Effect:** at a true Δ of 0.0225 the follow-up triggers only about half the time. Triggering it with 80% probability needs a true Δ of about 0.0225 + 0.84 × 0.0050 ≈ 0.0267.
   - **Missing caveat:** the claim-power table (lines 231-237) leaves out "before any design effect from clustering", which section 6 states for the plain detectable effect (line 220).
   - **Fix:** add one sentence for each point.

3. [severity: minor] prereg.md:153-168. The evidence that expected expiration is not rewritten at settlement is weakest for MLB, and MLB is 46% of the primary sample (4,635 of 10,059).
   - **The gap:** 7.0% of MLB expected-expiration times (about 330 games) are not on a 5-minute mark, and section 3 does not explain them.
   - **The live comparison:** 34 markets, with no sport split given. On 2026-10-22, MLB had at most World Series games, so MLB is probably about one game here (first review, finding 4).
   - **Worst case, and why this is minor:** suppose those values were rewritten to the end of play. Then t = end − 4 h, which is still before the first pitch unless the game ran past about 4 h. Only a handful of games could be affected, each with weight about 1/10,059.
   - **Fix (timing fields only):** for MLB markets off the 5-minute grid, report the distribution of close − expected (a rewrite to settlement would put it near 0), or explain the pattern (for example, 7:07 pm starts). State which sports the 34 live markets cover.

4. [severity: minor] research/studies/kalshi_calibration.py:97-99. The "no expected expiration" exclusion is still not in the pre-registration.
   - Its count must be 0 (10,331 − 59 − 109 = 10,163), so it changes nothing.
   - This is the third review to note it.
   - **Fix:** add one line to rule 4 in section 2.

5. [severity: minor] Process. Fixing findings 1 or 2 in code changes the script commit cited at prereg.md:4 and :332.
   - The pre-registration must then cite the new commit, and the diff should get a short re-check before the run.
   - docs/validation_log.md:887-902 still describes revision 2. It does not mention the second review or revision 3.

Status of the second review's findings:

| # | Finding | Status |
|---|---|---|
| 1 | Power ignored the claim rule | **Resolved.** I recomputed the table at prereg.md:231-237 (sd 0.5, z 1.96).<br>- **Smallest claimable \|Δ̂\|:** pooled 0.0187, Football 0.0476, Basketball 0.0297, Hockey 0.0254, Baseball 0.0195. All ✓.<br>- **Pooled power at 0.02:** 0.60 ✓, and 0.66 at sd 0.469 ✓.<br>- **Δ for 80% claim power:** pooled 0.0229 ✓, Hockey 0.0355 ✓, Basketball 0.0405 ✓.<br>- **Method difference:** the power and "Δ for 80%" columns evaluate B at the true Δ, while "smallest claimable" uses the estimate. That method reproduces the printed 0.23, 0.0570 and 0.0258. Using the estimate throughout gives 0.22, 0.0567 and 0.0257. The difference is immaterial.<br>- **Acceptance:** the lower power is accepted explicitly (prereg.md:239-246). Residuals are findings 1 and 2. |
| 2 | Follow-up threshold computed at run time | **Resolved.** It is a constant: `FOLLOWUP = 0.0225` (script:47), used at :303. The run-time half-spread is only printed (:357, :393). |
| 3 | The null interval ignored B; the reason could be wrong | **Resolved.**<br>- The null is widened by B (script:296-298).<br>- `claim()` (:278-286) separates "bootstrap interval itself includes 0" from "widened interval includes 0", matching prereg.md:286-291. |
| 4 | Expected expiration might be rewritten | **Resolved with a residual.** The evidence is at prereg.md:153-168: 34 of 34 live values identical, and schedule-like 5-minute marks. The MLB residual is finding 3. |
| 5 | Untested paths | **Resolved.**<br>- B printed: test:175-179.<br>- Single-date group (25 games, 1 date, p = 1, INSUFFICIENT): test:182-187. I checked that all 25 decision times fall on one Los Angeles date and pass the guard.<br>- Every claim and verdict path: test:190-205.<br>- A leak on an excluded decision: test:223-238. Game 5 is caught only by the run-wide check at script:323, not by `harness.score`.<br>- The spread rule in the tested price: test:241-247.<br>- The printed "Met./Not met." line is covered only through `followup_met`. I accept that: it is a one-line conditional. |

The first review's findings still hold at `98f8b3a`:

| # | Status |
|---|---|
| 1 | Every scored, rule-4-excluded and exploratory decision is checked, with no sampling (script:315-323). |
| 2 | Bound and claim rule: script:231-237 and :273-286. The algebra holds: the per-game shift is at most 1 − p_dog + \|Δ\|. |
| 3 | Weather t comes from the ticker date at local midnight (script:130). The zone map is correct for all seven cities. |
| 4 | Timing quantiles for all five series (prereg.md:124-130). |
| 5 | The null wording widens the interval by B (script:296-298). |
| 6 | Approval recorded (validation_log:887-892). |
| 7 | Counts per rule given (prereg.md:92-99). |
| 8 | "Latest two-sided candle" (prereg.md:92, :170-172) matches harness.py:76-81 and script:163-172. |
| 9 | sd 0.50 (script:43). |
| 10 | Weather metric explained and sd 0.0709 used (prereg.md:186-193; script:44, :410-416). |
| 11 | Weather clusters on the measured date (script:134). |
| 12 | Always five tests; untestable groups get p = 1 (script:353-356). |
| 13 | δ is fee only with multiplier 1 (prereg.md:215-216). `config/fee_schedule.json` is still absent from this checkout, so the multiplier stays an assumption, as stated. |
| 14 | Labels at script:251 and :428. |

Checklist:

1. **Leakage:** no leakage found in the prices. The selection residuals are disclosed and bounded.
   - **Decision time:**
     - Sports t is expected_expiration − 4 h (script:101). Whether that field was rewritten at settlement is covered by indirect evidence (finding 3).
     - Weather t is local midnight of the ticker date (script:130). The NWS daylight-time climate day starts at 01:00 local daylight time, so t is never late.
   - **Quote filter:** `ts_utc <= t AND ts_utc >= t − 6 h` (harness.py:80). `<=` is correct, because `end_period_utc` is the end of the hour (backfill.py:341).
   - **Isolation:** inserts only rows ≤ t and has no outcomes table (harness.py:129-140). Every decision is checked (script:323).
   - **Fields read:** only bid and ask closes from candles (script:154-155). Markets exclude every settlement field (:77). `price_close`, `settlement_value` and `settlement_utc` are never loaded. `result` is read only for rule 7 and for scoring (:156-157, :178).
   - **Inclusion:**
     - Rule 4 reads `close_utc`. This is disclosed (prereg.md:142-148) and bounded by B (script:231-237). B covers all 124 excluded games that have a price. The 44 without a price would fail rules 5–6 anyway.
     - "Settled" survivorship is benign: every game settles, and voids are dropped independently of y.
     - Flipping outcomes leaves the Inclusion section unchanged (test:122-130).
     - The backfill's candle window uses `close_utc`, but t is far inside it.
2. **Unit of N:** no issue found.
   - Sports use one market per game, the smaller ticker (script:97). Weather is collapsed to city-days (script:223-228).
   - Weather brackets are labelled "not independent" (:428).
   - Counts reconcile:
     - 2 × 10,331 + 15,372 = 36,034 market rows;
     - rule-4 exclusions per sport are 108 + 9 + 5 + 46 = 168;
     - 2,158 + 1,523 + 1,743 + 4,635 = 10,059 = 8,245 + 1,814;
     - weather 15,342 − 810 − 214 = 14,318.
3. **Clustering:** no issue found.
   - The cluster-robust SE uses the G/(G−1) factor (script:204-208).
   - The bootstrap resamples whole clusters, sorted and seeded (stats.py:123-133).
   - Sports cluster on the Los Angeles date of t, pooled across sports, so cross-sport correlation on the same date is handled. Weather clusters on the measured date across all cities.
   - Power ignores the design effect. That is stated for the detectable effect but not for the claim table (finding 2).
4. **Power:** recomputed, all correct. The multiplier z₀.₉₇₅ + z₀.₈ is 2.80159.
   - n_for_effect(0.5, 0.02) = ⌈4,905.6⌉ = 4,906 ✓.
   - Detectable effect: pooled 0.0140 ✓; Football 0.0302 ✓; Basketball 0.0359 ✓; Hockey 0.0336 ✓; Baseball 0.0206 ✓; Weather 0.0709 × 2.80159 / √2,557 = 0.0039 ✓.
   - The claim-power table is verified above. Both sds come from prices or bounds, not outcomes.
   - N < 20 or a single date gives INSUFFICIENT with p = 1 (script:353-355, :400-404).
5. **Multiple testing:** no issue found.
   - The tests that could be reported are: 1 primary at α 0.05, the 5-test BH family (always m = 5, script:355), and 2 exploratory tests.
   - The exploratory p values are computed but not printed, and the section is labelled "not in any test family" (:432-437).
   - The Wilson intervals are labelled unadjusted, not tests.
   - The harness paired differences are not printed (score_md, :262-270).
6. **Pre-registration integrity:** script against pre-registration.
   - **Matches:** rule order; the tested price (`FilteredMid`, two-sided, ≤ 6 h, spread ≤ 0.10); the guard values; the fold at p ≤ 0.5; weather p < 0.5; the bound formula; the claim rule; FOLLOWUP 0.0225; the bucket edges; seed 20261022; 2,000 resamples; α and q 0.05; WEATHER_SD 0.0709.
   - **One difference:** the unregistered zero-count exclusion (finding 4).
   - No `result.md` exists in the registry folder. The dataset id `34babdc762e232b0` matches dataset_manifest.json:2.
   - **NOT CHECKED, because access to git metadata and to the frozen folder was denied:**
     - that `98f8b3a` precedes `be61302`;
     - that `research/harness.py` is unchanged since `334c293`;
     - the manifest sha256 `db0b979f…`;
     - that the frozen manifest matches the committed copy.

     Three reviews in a row could not verify these. Before the run, the owner should check them directly with `git log --oneline`, `git diff 334c293 98f8b3a -- research/harness.py` and `python -m research.datasets verify <dataset dir>`.
7. **Determinism:** no issue found.
   - Sets are sorted before use (script:92, :323; harness.py:157, :177).
   - Funnels are printed sorted (:369-372).
   - Dict orders come from fixed tuples or from sorted input.
   - The bootstrap is seeded over sorted clusters.
   - DuckDB runs single-threaded with ORDER BY on insert, and candles are unique per (ticker, end_period_utc) (backfill.py:426).
   - No wall-clock time or temporary path reaches the output, and every float uses a fixed format (:242-246).
   - The byte-identity test (test:97-102) runs in one process. I found no hash-order dependence.
8. **Reporting honesty:**
   - Null, rejected-not-claimed and claimed paths are all written with equal care (script:289-298).
   - "Not a trading result" and "no result here is an edge" are stated (:438-443; prereg.md:51-55).
   - Calibration is never presented as profit.
   - Residuals: the power line in the result (finding 1) and the meaning of the follow-up threshold (finding 2).
9. **Other:**
   - **The guard has a small residual gap.** For football, t is inside the game only if the game ran 3.5 h or more from start to close and expected expiration sat 4 h or more after the start, 1 h later than the live 160–180 min. This is rare, as the second review noted.
   - **The harness spec is still out of date.** docs/specs/harness_spec.md:25 still describes the unit cluster as "the Los Angeles date of the decision". Weather uses the measured date. That is harmless, but the spec should say "as the study defines it".
   - **An empty sample would crash rather than report.** `statistics.median` (script:357) and `cluster_test` (:342) would raise on an empty scored set. That cannot happen with this dataset.
