# Review: 2026-10-kalshi-calibration, prereg

- Reviewed commit: `a7ca1ab98b8b47280c28b4054dac4b00e843620f`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T08:49:15Z
- Saved verbatim below; not edited.

---

VERDICT: APPROVE

Summary: The claim holds. The only script changes are the two new constants and three new printed passages, plus one appended test. No rule, threshold or computation moved. Revision 4 answers all five of the third review's minor findings. Two new minor issues remain, both in text and records, not in code: one example in section 3 does not add up, and the validation log's "VERIFIED" git checks name the old script commit. I still have no git access (reading `.git` was denied again), so I confirmed "no other change" by matching line positions, not by reading a diff.

Findings:
1. [severity: minor] prereg.md:184-191 and :136-138. Two wording errors in the new MLB evidence.
   - **The example does not add up.** Off-grid MLB expected expirations "fall on minutes ending in 2 or 3. That fits first pitches such as 7:07 plus 3 h." But 7:07 + 3 h = 10:07, which ends in 7. The pattern fits a 7:07 start only if the offset is about 2 h 55 min (7:07 + 2:55 = 10:02).
   - **Why it is minor:** the conclusion does not rest on the example.
     - Values bunched on two minute digits are a schedule pattern; a rewrite to the end of play would scatter.
     - Their close − expected spread (median −0.06 h, p5 −0.54, p95 +0.69) matches the other MLB games.
     - Only 6.7% fall within 2 minutes of the close.
   - **A second slip:** lines 136-138 say MLB was "about one game" of the 39 live games, while line 190 gives MLB 0 of the 34. That is consistent but vague.
   - **Fix:** state the offset actually seen (for example, "start + 2 h 55 min") or drop the example. Give the exact MLB count in the 39.
   - **Timing:** this text is not written into `result.md`, so it can be fixed with no script change.
2. [severity: minor] docs/validation_log.md:908-914. The "VERIFIED by direct checks" list covers the revision 3 commits, not revision 4:
   - `98f8b3a` precedes `be61302`;
   - `git diff 334c293 98f8b3a -- research/harness.py` is empty.

   The script that will run is `586ca79`, registered in `a7ca1ab`.
   - prereg.md:6-7 says the harness diff from `334c293` is empty, but not at which commit.
   - I could not run git.
   - Mitigating evidence: every harness line the third review cited still holds the same code (harness.py:76-81, :129-140, :157, :177; stats.py:123-133).
   - **Fix:** before the run, the owner should run `git log --oneline` (confirm `586ca79` precedes `a7ca1ab`) and `git diff 334c293 586ca79 -- research/harness.py`, and log both with the date.

Checklist:
1. **Leakage:** no change since the third review.
   - The decision-time code is unchanged: sports t at script:107, weather t at :136.
   - So are the quote filter `ts_utc <= ? AND ts_utc >= ?` (harness.py:80), the all-decision leakage check (script:321-329) and the isolation insert `<= t` (harness.py:135-139).
   - Only bid and ask closes are read from candles (:160). `result` is read only for rule 7 and scoring (:162-163, :184).
   - The new lines read only constants.
   - The MLB evidence (finding 1) supports, but does not prove, that expected expiration is not rewritten at settlement. That residual is disclosed (prereg.md:192-194).
2. **Unit of N:** no change. One market per game, the smaller ticker (script:103). Weather is collapsed to city-days (:229-234). Weather brackets are labelled "not independent" (:443).
3. **Clustering:** no change.
   - The cluster-robust SE with G/(G−1) is at script:210-214.
   - The bootstrap resamples whole sorted clusters with a seed (stats.py:123-133).
   - The new printed power text now carries "before any design effect" (script:395, :396, :423), which answers the third review's finding 2 in the result as well as in prereg.md:273.
4. **Power:** I recomputed the new constants. SE = 0.5/√10,059 = 0.004985; B = 0.0087 + 0.01218·|Δ| (c = 124/10,183).
   - **Pooled:**
     - smallest claimable (0.009771 + 0.0087)/0.98782 = 0.0187 ✓;
     - power at 0.02: Φ(0.26) = 0.60 ✓;
     - 80% claim power at 0.0187 + 0.8416 × 0.004985 = 0.0229 ✓.
   - **Football:** smallest claimable (1.96 × 0.010764 + 0.0249)/0.96598 = 0.0476 ✓.
   - **The other per-sport constants** at script:50-52 match prereg.md:259-263 digit for digit.
   - **FOLLOWUP_80:** 0.0225 + 0.8416 × 0.004985 = 0.0267 ✓ (script:53, prereg.md:276). The 0.0225 cut binds because it is above the 0.0187 claim bar.
   - **Unchanged:** `n_for_effect` (stats.py:145-149) and the INSUFFICIENT path (script:359-361, :412-416).
5. **Multiple testing:** no change. The family is always five tests with BH q = 0.05 (script:359-362). The new lines are descriptive constants, not tests. The exploratory section is still labelled "not in any test family" (:447).
6. **Pre-registration integrity:**
   - **How I verified the script claim:** every line the third review cited sits at a shift that matches exactly four insertions, totalling 15 lines:
     - +6 at script:48-53 (CLAIM_POWER, FOLLOWUP_80). For example, old :77 → :83, :97 → :103, :101 → :107, :130 → :136, :204-208 → :210-214, :231-237 → :237-243, :273-286 → :279-292, :296-298 → :302-304, :323 → :329, :353-357 → :359-363, :387-389 → :393-395.
     - +4 at :396-399 (pooled claim power), so old :393 → :403.
     - +2 at :404-405 (follow-up caveat), so old :400-404 → :412-416.
     - +3 at :423-425 (per-sport claim power), so old :428 → :443, :432-443 → :447-458.
   - **What is unchanged:** FOLLOWUP (:47), SD_ASSUMED (:43), WEATHER_SD (:44), the seed, N_BOOT, ALPHA, Q, MAX_AGE, MAX_SPREAD, MIN_CLOSE_GAP and the bucket edges all sit before the first insertion, at the lines the third review cited. `claim`, `survives`, `primary_verdict`, `followup_met` and `exclusion_bound` have the same bodies.
   - **Tests:** the test file's cited lines (97-102, 122-130, 175-247) are unchanged. The new test is appended at :250-256.
   - **Status:** the claim is INFERRED from these line positions, not VERIFIED by diff, since I had no git access.
   - **The third review's five findings:**
     - (1) claim power is printed at :396-399 and :423-425, and tested at test:254-255 ✓;
     - (2) the follow-up and design-effect caveats are at prereg.md:273-277 and script:403-405 ✓;
     - (3) the MLB off-grid evidence and the live sports split are at prereg.md:184-191 ✓, with the wording issue in finding 1;
     - (4) the zero-count exclusion is at prereg.md:105-106, matching script:104-106 ✓;
     - (5) the new commit is cited at prereg.md:4 and :363, and the log is updated at validation_log.md:903-907 ✓, with the commit gap in finding 2.
   - **Dataset id:** `34babdc762e232b0` in prereg.md:75 matches dataset_manifest.json:2.
   - **No result yet:** there is no `result.md` in the registry folder.
7. **Determinism:** no issue found. The new text formats fixed constants with fixed precision (`:.4f`, `:.2f`). The per-sport line iterates the `SPORT_GROUPS` tuple (:424-425), not a dict. There is no wall-clock time or randomness. The byte-identity test (test:97-102) also covers the new lines.
8. **Reporting honesty:** this is improved.
   - The result will now show next to each other: the plain detectable effect, the claim power, and the fact that the follow-up triggers with 80% probability only at Δ ≈ 0.0267.
   - A non-claim is explicitly to be "read with these numbers" (:399).
   - "Not a trading result" and "no result here is an edge" are still printed (:454-458).
9. **Other:**
   - Rule 4's zero-count "no expected expiration" exclusion and rule 2's zero exclusion will not appear in the result's Inclusion section: the funnel is a `defaultdict` and prints only keys that were incremented (script:91, :375-378). The totals still add up (games, minus 59, minus 109, equals games with a decision time). This was already the case, and it does not affect the conclusion. Printing every registered rule, including zeros, would be clearer.
   - The third review's other residuals are unchanged, and none blocks the run:
     - the small guard gap for football;
     - the harness spec's cluster wording;
     - a crash rather than a report on an empty sample.
