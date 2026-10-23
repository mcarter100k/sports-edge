# Review: 2026-10-kalshi-calibration, result

- Reviewed commit: `e690dfbcda69d2a565bd516adda31c5a8c7e24e6`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T09:22:30Z
- Saved verbatim below; not edited.

---

VERDICT: APPROVE

Summary: The note answers all seven findings from the first result review. Its numbers match `result.md` wherever they overlap, and it does not claim an edge. The one major finding from that review is resolved: the note gives the weather claim its registered consequence, "a clear but untradeable miscalibration. Record it.", and it says the closing line of `result.md` does not apply to weather. With the note, the result is ready for the owner. The remaining findings are minor:
- one factual slip (rule 7 does apply to weather);
- an incomplete list of zero-count rules;
- post-result figures that no committed code can reproduce;
- a run-record sentence that compares two figures averaged in different ways.

Findings:

1. [severity: minor] result_note.md:64-91; docs/validation_log.md:938-941. **No committed code can reproduce the post-result figures.**
   - **Which figures:** the Σ(y − bid), median half-spread and "share with bid 0.01" columns, and the count of 6 at :109. No file under `research/` computes any of them. I searched `research/studies/` (only `kalshi_calibration.py` and `match_coverage.py` are there) and searched for the expressions involved.
   - **What I could check:**
     - I recomputed the Σ(y − p) column from the bucket table in `result.md` (:139-144), and every row matches within rounding. For example, 4,770 × (0.0111 − 0.0251) = −66.8, and the note gives −66.6.
     - The brackets add up to 13,471, and the column totals −148.4.
     - The −0.0018 follows from the note's own bid column: −23.9 / 13,471.
   - **What I could not check:** the bid, spread and bid-0.01 columns themselves. They are NOT CHECKED.
   - **Why it matters:** these figures support "untradeable". The run record repeats one of them, and nobody can rerun it.
   - **Fix:** commit the query that produced them as a report-only script in `research/studies/`, or paste it into the note. Label each figure under AGENTS.md rule 10 (OBSERVED or INFERRED).

2. [severity: minor] docs/validation_log.md:938-941. **"Measured against the bid it nearly vanishes (−0.0018 per bracket)" compares figures averaged in different ways.**
   - Δ = −0.0124 averages per city-day first, so each bracket is weighted 1/k.
   - −0.0018 is a plain average over brackets.
   - The like-for-like comparison is −148.4 / 13,471 = **−0.0110 per bracket against the mid**, then **−0.0018 against the bid**. The conclusion holds, but the sentence mixes the two scales.
   - **Fix:** quote −0.0110 → −0.0018, both per bracket.

3. [severity: minor] result_note.md:84-91. **The direction and the after-fee sign of −0.0018 are not stated, and "close to nothing" is a judgment on a post-hoc figure.**
   - **The direction:** −0.0018 is the mean of (y − bid). So a seller at the bid would have *earned* about 0.18 cents per bracket, before fees. A reader can easily take the minus sign as a loss.
   - **After fees, pooled:** the modelled fee averaged over these brackets is about 0.78 cents. That figure is INFERRED from the note's bucket counts, the mean prices in `result.md` and 0.07 × p(1 − p) at multiplier 1. So selling at the bid loses about 0.6 cents per bracket on average after fees. Saying so is plainer than "close to nothing".
   - **After fees, sub-0.30 brackets:** the same arithmetic gives about +1.06 cents before fees and about +0.5 cents after the modelled fee (INFERRED).
     - The note rightly calls choosing that slice post-hoc (:90-91).
     - It should still say plainly that this slice is the obvious post-hoc candidate. It is a hypothesis, testable only under a new pre-registration on data after the dataset's last date.
     - Kalshi may round the fee up to the next cent per order. That is INFERRED and not in any repo file. If so, the lowest bucket's apparent gain disappears.
   - **Fix:** state the sign, the after-fee pooled figure, and the sub-0.30 caveat. Label all three INFERRED.

4. [severity: minor] result_note.md:111-113. **"For weather … neither applies" is wrong for rule 7.**
   - `result.md:21` shows 2 weather brackets excluded as "not settled yes or no". That exclusion is decided by reading outcomes and is not covered by B, which is 0 for weather.
   - Its effect is at most about 2/2,557 ≈ 0.0008, against an interval whose upper end is −0.0097. The claim is unaffected.
   - "t is about a day before close" is also stated without a label (INFERRED).
   - **Fix:** say that rule 7 removed 2 weather brackets, give the bound of about 0.0008, and say the claim stands.

5. [severity: minor] result_note.md:117-120. **There are three zero-count rules, not two.**
   - The third is "excluded: not a two-market binary game" (kalshi_calibration.py:101). It is also missing from the funnel.
   - Its count is 0 by the same arithmetic: 10,331 games, 10,163 with a decision time, and 59 + 109 printed.
   - **Fix:** list all three.

6. [severity: minor] result_note.md:107-110. **The count of 6 for the backfill window may be slightly low.**
   - **Why:** the window starts at close − 14 days, floored to the hour (backfill.py:188-192). A game with close − t between about 14 d − 6 h and 14 d still has candles at t, but loses the earlier part of its 6-hour lookback. Rule 5 could then exclude it, and B would not cover it.
   - The 6 counts only close − t > 14 d.
   - The effect is negligible, and it can only widen an interval that is already not claimed.
   - **Fix:** recount with close − t > 14 d − 6 h, or say why the edge case does not apply.

7. [severity: minor] result_note.md:30-33 against :111. **The note's own extra widening is not carried into its interval reading.**
   - The note reads "a favorite-longshot bias larger than 0.0065 lies outside it", then says exclusions not covered by B could widen the interval by about 0.0017 more.
   - Read consistently, the figure is about 0.0082.
   - **Fix:** use the wider figure, or say the 0.0065 excludes rule 7 and the window.

8. [severity: minor] docs/validation_log.md:925-927, :945-963. **The git evidence comes from the author, and a clean working tree at run time is not recorded.**
   - I again could not read git metadata (the read was denied). Commit order (`586ca79` → `a7ca1ab` → `f81c74c` → `1e2e506`) and the diffstat are NOT CHECKED by me.
   - `git diff --stat 586ca79 HEAD` shows committed changes only. An uncommitted edit to the script at run time would not appear.
   - **Fix (owner):** run `git log --oneline` yourself. Rerun the script from a clean checkout of `586ca79` and confirm sha256 `b8f8cd29…`. That one rerun settles both points.

9. [severity: minor] result_note.md:41-44 and :81-83. **Two statements need labels or gentler wording.**
   - "Almost the same games" for the 5 h and 8 h runs is INFERRED from the counts. The overlap was never computed.
   - "Its mid is set by the quote format, not by a view" overstates the case:
     - only 46% of that bucket has a bid of 0.01, which is nearly half, not most;
     - a 0.01/0.02 quote still bounds the price;
     - that the tick floor causes the deficit is an inference, not a measurement.

Checklist:

1. **Leakage** — no new issue in the registered result.
   - **Code unchanged:** per the author's diffstat (validation_log.md:954-963), no code changed since `586ca79`. The harness filter `ts_utc <= t AND ts_utc >= t − 6 h` is unchanged (harness.py:80). The isolation connection inserts only rows `<= t` and has no outcomes table (harness.py:133-139). `price_close` is never read (kalshi_calibration.py:160-161).
   - **The post-result figures** read outcomes after the result. The note says so (:9-11). They do not feed back into `result.md`.
   - **Exclusions after t** (rule 7 and the backfill window) are now counted, with the gaps in findings 4 and 6.

2. **Unit of N** — no issue found.
   - The note reports games and city-days for the tests, and brackets only in the descriptive table.
   - I re-reconciled the counts:
     - 15,342 − 810 − 214 − 2 = 14,316;
     - 13,471 below 0.50 + 845 at or above 0.50 = 14,316;
     - p = 0.50 falls in the 0.50–0.60 bin (stats.py:40-47), so the sub-0.50 buckets match the weather metric's `p < 0.5` (script:232).
   - The note's table could repeat "(not independent)" in its header.

3. **Clustering** — no change. The cluster SE uses G/(G−1) (script:214), and the bootstrap resamples whole clusters (stats.py:123-133). The note adds no intervals.

4. **Power** — recomputed:
   - n_for_effect(0.5, 0.02) = ⌈(2.80158 × 25)²⌉ = 4,906 ✓;
   - detectable_effect(0.5, 10,048) = 0.0140 ✓;
   - weather: 2.80158 × 0.0709 / √2,557 = 0.0039 ✓.

   No test group has N < 20. The note flags the five cells under 20 (:93-98), answering first-review finding 5.

5. **Multiple testing** — no issue found. The note adds no tests and no p values. Its bucket-level statements are descriptive, and it names the sub-0.30 slice as post-hoc (:90-91).

6. **Pre-registration integrity** — each first-review finding against the note:
   - **F1, major:** resolved (:46-62). The wording matches prereg.md:329-330. The note states that no follow-up is registered and that the 0.0225 threshold is on a different scale. It says a follow-up needs its own pre-registration, priced at the bid after fees.
   - **F2:** resolved (:59-91). New minors: findings 1-3 and 9.
   - **F3:** resolved (:17-40). I checked the numbers against result.md:
     - the widened interval is [−0.0065, +0.0294];
     - p 0.0166 rounds to 0.017;
     - the four sports run from +0.0091 to +0.0141;
     - the 0.00–0.05 bucket is 0.0270 against 0.0123, 234 of 243 games football;
     - 5 of the 6 buckets from 0.15 to 0.45 are underpriced, so "most" is right.

     New minor: finding 7.
   - **F4:** resolved (:41-44). The counts 10,082 and 9,838 and the estimates +0.0117 and +0.0129 match result.md:170-171.
   - **F5:** resolved (:93-98). All five cells match result.md.
   - **F6:** resolved, with gaps (:103-113):
     - 11/10,059 = 0.0011 ✓;
     - 6/10,054 = 0.0006 ✓;
     - the two together give 0.0017 ✓.

     New minors: findings 4 and 6.
   - **F7:** resolved:
     - the run record is in the log (:923-963), with the sha256 and the second identical run;
     - the script commit is named (note :124);
     - the zero-count weather rule is disclosed.

     The list of zero-count rules is incomplete (finding 5), and git is NOT CHECKED (finding 8).
   - **Dataset id:** `34babdc762e232b0` matches in result.md:5, prereg.md:75 and dataset_manifest.json:2.

7. **Determinism** — `result.md` is unchanged and its generation is deterministic: seeded bootstrap (script:216), sorted sets, single-threaded DuckDB, fixed float formats. The note's post-result figures have no committed code (finding 1).

8. **Reporting honesty** — no overclaim of an edge anywhere in the note.
   - The non-claimed sports result, the claimed weather result and the small cells get equal care.
   - The post-result section is labelled "not evidence" (:9-11, :64). Two places still lean on a post-result figure:
     - "close to nothing" (:86);
     - "nearly vanishes" in the run record (validation_log.md:940).

     Both point toward the registered "untradeable" reading, so neither inflates a claim. They should still be stated as plain, labelled figures (findings 2 and 3).

9. **Other.**
   - `config/fee_schedule.json` does not exist in this worktree; the only files in `config/` are `slate_sports.json`, `sport_groups.json` and `team_aliases.json`. The note's "not in any cited fee table" (:87) is therefore true. Every fee figure here rests on 0.07 × p(1 − p) with the multiplier assumed 1.
   - The harness merge is gated on this review (validation_log.md:889-891). Nothing in this review blocks it.
   - **Ready for the owner:** yes, read together with the note. The findings above are corrections to the note and the log; none changes a registered conclusion.
