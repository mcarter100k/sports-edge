# Review: 2026-10-kalshi-calibration, result

- Reviewed commit: `8ee60c77de4c98b818437f5d7b65f8368aa4bd7a`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T09:28:06Z
- Saved verbatim below; not edited.

---

VERDICT: APPROVE

Summary: Of the nine minor findings in the second result review, six are answered at 8ee60c7, and they are the six the caller meant (1 to 6). The new script `kalshi_calibration_postresult.py` computes what the note says it computes, and the note's figures agree with each other and with `result.md`. Findings 7 and 9 are still open in `result_note.md`, and the run record says "six" findings were answered when the review listed nine. These are wording fixes, and none changes a registered conclusion.

Findings:

1. [severity: minor] result_note.md:30-33 against :129-130. **Second-review finding 7 is not answered.**
   - Line :31-32 still says "A favorite-longshot bias larger than 0.0065 lies outside it."
   - Line :129 says the exclusions not covered by B could widen the interval by about 0.0017 more.
   - Read together, the figure is about 0.0082.
   - **Fix:** say 0.0082, or add "(before rule 7 and the candle window)" after 0.0065.

2. [severity: minor] result_note.md:41-44 and :86-88. **Second-review finding 9 is not answered.**
   - "They use almost the same games" still has no label. Nobody computed the overlap, so it is INFERRED from the counts.
   - "so its mid is set by the quote format, not by a view" is unchanged. Only 46% of that bucket has a bid of 0.01. A 0.01/0.02 quote still limits the price. Nothing measured that the tick floor causes the deficit.
   - **Fix:** label the first statement INFERRED. Change the second to "nearly half of it is quoted at the tick floor, which may hold its mid up (INFERRED)".

3. [severity: minor] docs/validation_log.md:972-975. **The run record miscounts the findings and says all were answered.**
   - It says "Its six minor findings are answered". The second review listed nine minor findings (r2:19-76).
   - Findings 7 and 9 are open (findings 1 and 2 above).
   - Finding 8 is for the owner to do, and the record does not mention it.
   - **Fix:** append a dated line: nine findings; 1 to 6 answered; 7 and 9 answered by the next note edit; 8 is for the owner (see finding 6 below).

4. [severity: minor] result_note.md:97. **"Across all brackets under 0.50, there was nothing to earn after costs" states a post-result point estimate as plain fact.**
   - The figure behind it (−0.56 cents per bracket at multiplier 1) has no interval.
   - The brackets are not independent: several brackets share one city-day.
   - The conclusion points the conservative way, so it inflates nothing.
   - **Fix:** "the point estimate after the modelled fee is a loss of 0.56 cents per bracket (no interval; brackets are not independent)". Add "(not independent)" to the "Brackets" header of the table at :74, as the second review suggested (r2:91).

5. [severity: minor] result_note.md:66-67; docs/validation_log.md:965-971. **The figures are labelled OBSERVED, but the script's printed output is not recorded anywhere.**
   - The code is now committed, so the figures can be rerun. That answers finding 1.
   - But no log entry shows the actual output, the command, or the commit it ran at. AGENTS.md rule 11 asks for actual output in a run report.
   - I could not run the script, so to me the bid, half-spread and bid-0.01 values are reproducible but NOT CHECKED.
   - **Fix:** paste the script's output into the validation log, with the command and commit.

6. [severity: minor, owner action] docs/validation_log.md:945-963. **Second-review finding 8 still stands.**
   - The git order and the clean working tree at run time rest on the author's evidence.
   - I cannot read git metadata. Commit 8ee60c7, and the claim that `result.md` is unchanged since 1e2e506, are NOT CHECKED by me.
   - INFERRED: every `result.md` line the second review cites still holds the same content (for example :21, :139-144, :170-171).
   - **Fix (owner):** run `git log --oneline` and `git diff 1e2e506 8ee60c7 -- research/registry/2026-10-kalshi-calibration/result.md` (it should be empty). Rerun the study from a clean checkout of `586ca79` and confirm sha256 `b8f8cd29…`.

How each second-review finding was answered:

- **Finding 1 (no code for the figures):** answered. The script is committed, and I read it against the note:
  - **Bid.** The bid is computed as `p − half_spread` (script:47, :53). Since p = (bid + ask)/2 and half_spread = (ask − bid)/2 (kalshi_calibration.py:178, :197), that equals the bid.
  - **Buckets.** They use `lo <= p < hi` on the mid, over the edges 0, .05, .10, .20, .30, .40, .50 (script:18, :50). These match the `result.md` weather buckets (:139-144).
  - **Fee.** The fee is 0.07 × bid × (1 − bid), charged at the sale price (script:21-23, :54).
  - **Seller figures.** The seller's figure before fees is mean(bid − y), and after fees it is mean(bid − y − fee) (script:60-61).
  - **Arithmetic I checked from the note's own table:**
    - −148.4 / 13,471 = −0.0110 ✓;
    - Σ(y − bid) = −23.9, and −23.9 / 13,471 = −0.0018 ✓;
    - the sub-0.30 slice has 4,770 + 1,934 + 2,183 + 1,785 = 10,672 brackets ✓, with Σ(y − bid) = −112.7, so a seller gets +1.06 cents ✓;
    - the implied mean fees, 0.74 cents pooled and 0.51 cents for sub-0.30, are plausible for those price levels.
- **Finding 2 (figures averaged differently):** answered (note :89-92; log :965-971). Both figures are now per bracket, and the original record is kept with a correction appended.
- **Finding 3 (sign and after-fee figure):** answered.
  - The sign is stated (:92-93), and so is the after-fee pooled loss (:94-95).
  - The sub-0.30 slice is named as the post-hoc candidate. The note says it can only be tested under a new pre-registration on later data, priced at the bid after fees (:98-104).
  - The fee-rounding caveat is labelled INFERRED (:105-106).
- **Finding 4 (rule 7 applies to weather):** answered (:131-137). It gives 2 brackets, a bound of 2/2,557 ≈ 0.0008 against an interval upper end of −0.0097, and an INFERRED label on the timing of t.
- **Finding 5 (three zero-count rules):** answered (:141-145). I checked the arithmetic:
  - 10,331 − 59 − 109 = 10,163 ✓;
  - 15,342 + 30 = 15,372 ✓;
  - cross-check: 2 × 10,331 + 15,372 = 36,034, the markets row count (result.md:6). So no sports event had other than two markets, and no market fell outside these groups.
- **Finding 6 (the count of 6 may be low):** answered (:126-128; script:38-41).
  - `timedelta(days=14, hours=-6)` is 13 d 18 h.
  - The backfill floors the window start to the hour (backfill.py:190-191), which only moves the start earlier, so the 14 d − 6 h test covers every affected game.
  - Losing part of the window can only remove quotes. It cannot change which quote is the latest one at or before t, so rule 5 is the only rule it can affect.

Checklist:

1. **Leakage** — no new issue.
   - **`result.md` and the study script:** unchanged, as far as I can see.
   - **The quote filter:** still `ts_utc <= t AND ts_utc >= t − 6 h` (harness.py:80).
   - **The new script:** reads outcomes after the result, through `k.priced`. The note labels this (:9-11, :64), and nothing feeds back into `result.md`.
   - **Exclusions decided after t:** rule 7 for sports and weather, and the candle window, are now all counted with bounds (:120-137).
2. **Unit of N** — the tests stay in games and city-days. The post-result figures are per bracket, and the table header does not say "(not independent)" (finding 4).
3. **Clustering** — no new intervals. The registered intervals are unchanged: cluster SE with G/(G−1) (kalshi_calibration.py:214), and a cluster bootstrap (:216).
4. **Power** — no change. Rechecked:
   - n_for_effect(0.5, 0.02) = 4,906;
   - detectable_effect(0.5, 10,048) = 0.0140;
   - weather: 2.80158 × 0.0709 / √2,557 = 0.0039.

   The cells under 20 are listed (:108-113). None of them is a test group.
5. **Multiple testing** — no new tests and no p values in the note or the script. The sub-0.30 slice is labelled post-hoc and a hypothesis (:98-104).
6. **Pre-registration integrity** — the weather consequence matches prereg.md:329. The dataset id `34babdc762e232b0` matches in result.md:5, prereg.md:75 and dataset_manifest.json:2. Git is NOT CHECKED by me (finding 6).
7. **Determinism** — the new script has no randomness. Its group counts are sorted (script:41), and it uses `statistics.fmean`/`median` with fixed formats and a temporary directory only. It prints and writes no files. One small difference: the script prints the half-spread with 4 decimal places (0.0050), and the note shows 0.005.
8. **Reporting honesty** — no edge is claimed anywhere.
   - The post-result section is labelled not evidence.
   - The one positive after-fee figure (+0.55 cents, sub-0.30) is presented as a post-hoc hypothesis with a fee-rounding caveat.
   - The remaining lapses (findings 1, 2 and 4) are wording, and each makes the result look neither better nor worse than it is.
9. **Other** — the new script is report-only and in the free zone. It changes no protected file. Nothing here blocks the harness merge that waits on this review.

The fixes are small edits to `result_note.md` (findings 1, 2 and 4) and appended lines in `docs/validation_log.md` (findings 3 and 5). Finding 6 needs you to run the git commands yourself.
