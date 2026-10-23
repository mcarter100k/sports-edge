# Review: 2026-10-kalshi-calibration, result

- Reviewed commit: `1e2e506f17547d2cd332c5d11481ed0d602670f6`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T09:15:43Z
- Saved verbatim below; not edited.

---

VERDICT: REVISE

Summary: The numbers check out, and the decision lines follow the registered rules. I recomputed the bound, the BH adjustment, the power figures and every count in the funnel. The sports result reads correctly as "rejected, not claimed", and the estimate points the opposite way from the favorite-longshot hypothesis. The weather result is the only claim, and the result file does not state its registered consequence ("a clear but untradeable miscalibration; record it"). Worse, its closing line points every claim toward a shadow test, which the registered rules reserve for |Δ| ≥ 0.0225. That is fixable without touching `result.md`: add a reviewed note. Several things a reader must know about what the weather claim means are also missing.

Findings:

1. [severity: major] result.md:49 and :176; kalshi_calibration.py:400-405, :418-421, :457-458; prereg.md:323-330. **The one claimed result has no registered consequence printed, and the closing line points it the wrong way.**
   - **What was claimed:** weather Δ −0.0124, interval [−0.0151, −0.0097], BH-adjusted p ≈ 0, B = 0. The claim is correct under the rule.
   - **What section 9 says follows:** "If claimed with |Δ| < 0.0225: a clear but untradeable miscalibration. Record it." Here |−0.0124| < 0.0225. The follow-up shadow study is reserved for |Δ| ≥ 0.0225, and it is worded for "games after this dataset's last date".
   - **What the result prints:** only "yes" in the claimed column. The follow-up check runs for the primary test only (script:400-405). The last line (result.md:176) says: "A claimed rejection is a hypothesis for a prospective, fee-adjusted shadow test." A reader will apply that to the only claim, which is weather.
   - **The threshold is also on a different scale for weather.** 0.0225 is per contract at p = 0.5 for one game. The weather value is a per-city-day mean over k brackets. Nothing registered makes weather tradeable, so the registered reading is "untradeable, record it".
   - **Why it matters:** the owner could start a weather shadow study believing it is the registered next step.
   - **Fix:** do not edit `result.md` (it is protected, and the script writes it). Add a dated, reviewed note next to it (or in `docs/validation_log.md`) that says: "Weather: claimed; |Δ| 0.0124 < 0.0225; a clear but untradeable miscalibration of the mid; recorded; no follow-up registered." Any weather follow-up needs its own pre-registration, with a cost threshold on the weather scale. In future study scripts, print the registered consequence for every claimed group.

2. [severity: minor] result.md:139-141 and :49. **What the weather claim does and does not mean.** A reader needs this before acting.
   - **The mid has a floor.** Rule 5 needs bid > 0, and the tick is 0.01 (prereg.md:50). So any two-sided quote has a mid of at least 0.015. The lowest weather bucket wins 0.0111, which is below the lowest possible mid.
   - **Where the deficit sits** (rough arithmetic from the bucket table, INFERRED):
     - the 0.00–0.05 bucket gives about 4,770 × (0.0111 − 0.0251) ≈ −67, out of a net of about −149 across the sub-0.50 brackets;
     - the 0.30–0.50 buckets go the other way, by about +57.
   - **So much of Δ sits where the price is set by the quote format.** These are brackets whose mid is pinned near the tick floor. The 0.10 spread cap also lets a mid sit far above the bid.
   - **Selling those brackets earns the bid, not the mid.** The weather spread is never printed: the half-spread is computed and shown for sports only (script:363, :403).
   - **What the result supports:** "the mid overstates the sub-0.50 brackets taken together". It does not support "weather traders overprice longshots", and it says nothing about a profitable sell.
   - **Fix:** put this in the note from finding 1. Any follow-up must price at the bid, after fees.

3. [severity: minor] result.md:33-35. **The sports decision line is correct but hard to read.**
   - **What it shows:** "H0 rejected, not claimed" in bold.
   - **What it leaves out:**
     - **The direction.** The estimate is +0.0112: underdogs won more often than priced, the opposite of the favorite-longshot hypothesis.
     - **The widened interval:** [+0.0024 − 0.0089, +0.0205 + 0.0089] = [−0.0065, +0.0294].
   - **What a reader must know:**
     - (a) The non-claim comes only from the worst-case bound. B assumes all 124 excluded games went the worst way.
     - (b) The widened interval contains the 0.0225 threshold. A reverse bias of tradeable size is not excluded. A favorite-longshot bias larger than 0.0065 lies outside the interval. That is not a registered "ruled out" statement, because that wording is registered for the not-rejected path only.
     - (c) Even a claim would not have triggered the follow-up, since 0.0112 < 0.0225. The non-claim costs no follow-up.
     - (d) All four sports are positive (+0.009 to +0.014). That is the same data split up, not separate evidence.
     - (e) The descriptive table changes sign by price. The lowest bucket is overpriced (0.0270 priced against 0.0123 won; 234 of its 243 games are football). The 0.15–0.45 range is mostly underpriced. This is not tested.
   - **Fix:** put this in the note. Future scripts should print the widened interval on every decision path.

4. [severity: minor] result.md:170-171. **The exploratory runs are not replications.**
   - The 5 h and 8 h runs use nearly the same games (10,082 and 9,838, against 10,048), priced a few hours apart. Their agreement with the primary (+0.0117 and +0.0129) is close to automatic.
   - They are correctly labelled "not in any test family". Nothing says they share games with the primary.
   - **Fix:** state it in the note, and do not count them as support.

5. [severity: minor] result.md:95, :112, :125, :149, :150. **Calibration cells with fewer than 20 units are not flagged INSUFFICIENT** (AGENTS.md rule 9):
   - Basketball 0.00–0.05: n 9;
   - Hockey 0.20–0.25: n 15;
   - Baseball 0.10–0.15: n 1, observed 1.0000;
   - Weather 0.90–0.95: n 11;
   - Weather 0.95–1.00: n 5.

   These are descriptive, but the n = 1 row in particular invites misreading. **Fix:** future scripts flag such cells. The note should say these cells carry no information.

6. [severity: minor] result.md:11-12; backfill.py:188-192. **Two exclusions that depend on information after t are not covered by B.**
   - **Rule 7 voids:** 11 games, decided by reading outcomes. Worst-case shift ≤ 11/10,059 ≈ 0.0011.
   - **The backfill's candle window is anchored on close_utc**, as [max(open, close − 14 d), close]. A game that closed more than 14 days after t would have no candles at t. It would fall out under rule 5 (88 games, 82 of them football) instead of rule 4, and escape B. NOT CHECKED: how many of the 88 this explains.
   - **Neither changes a conclusion.** For sports, both could only widen an interval that is already not claimed. For weather, t is about a day before close (INFERRED from the measured-date design), so the window cannot cut it off.
   - **Fix:** count, from timing fields only, the rule-5 exclusions with close − t > 14 days, and record the count.

7. [severity: minor] docs/validation_log.md:919-921; kalshi_calibration.py:91, :133-135, :375-378. **Records and unverified claims.**
   - **Git checks NOT CHECKED (no access).** Reading `.git` was denied again. That makes five reviews in a row unable to check commit order. I take on the caller's word, not verified, that:
     - `f81c74c` precedes the result commit;
     - `git diff 586ca79..HEAD` touches no code;
     - both runs gave sha256 `b8f8cd29…`.
   - **The run is not in the validation log.** There is no entry for the run itself: date, script commit, output sha256, and the second identical run.
   - **`result.md` does not name the script commit.**
   - **Zero-count rules are never printed** (the funnel is a `defaultdict`). That includes the weather "no measured date in the ticker" exclusion (script:133-135), which is not in the pre-registration. Its count is 0 by arithmetic (15,342 + 30 = 15,372).
   - **The post-approval wording fixes were not re-reviewed.** I checked them now: prereg.md:137-138 ("MLB was 1 of the 39") and :187 ("2 h 55 min") match the requests in the r4 review. I found no rule wording changed. That is INFERRED from the text, not from a diff.
   - **Fix:** the owner logs `git log --oneline` (showing `f81c74c` before the result commit), `git diff --stat 586ca79 HEAD`, and the run record in the validation log.

Checklist:

1. **Leakage:** no leakage found in the tested prices. The selection residuals are bounded or negligible.
   - **Decision times:**
     - Sports t = expected_expiration − 4 h (script:107). Weather t = local midnight of the measured date in the ticker (script:136), with correct zones for all seven cities (:31-33).
     - Whether expected expiration was rewritten at settlement rests on indirect evidence. That residual is disclosed at prereg.md:192-194.
   - **Quote filter:** `ts_utc <= t AND ts_utc >= t − 6 h` (harness.py:80). `<=` is right because `end_period_utc` is the end of the hour (backfill.py:341).
   - **The isolation connection** inserts only rows `<= t` (harness.py:135-139) and has no outcomes table.
   - **All 46,071 decisions were checked** (script:321-329; result.md:7). That covers scored, bound and exploratory decisions.
   - **Fields read:**
     - from markets, no settlement fields (script:83);
     - from candles, only the bid and ask closes; `price_close` is never read (:160-161);
     - `result` only for rule 7 and scoring (:162-163, :184).
   - **Inclusion depending on later information:**
     - rule 4 (close_utc) is bounded by B, applied correctly;
     - rule 7 and the backfill window are covered in finding 6;
     - "settled only" survivorship is benign, because every game market settles.
2. **Unit of N:** no issue found.
   - One market per game, the smaller ticker (script:103), folded to the underdog (:221).
   - Weather is collapsed to city-days (:229-234), and its brackets are labelled "not independent".
   - **Counts reconcile:**
     - sports: 10,163 − 88 − 16 − 11 = 10,048;
     - per sport, 2,158/1,523/1,743/4,635 become 2,152/1,520/1,742/4,634, which accounts for the 11 voids;
     - weather: 15,342 − 810 − 214 − 2 = 14,316.
   - N is reported as games and city-days.
3. **Clustering:** no issue found.
   - The cluster-robust SE uses G/(G−1) (script:214). The bootstrap resamples whole sorted clusters (stats.py:123-133).
   - Sports: 542 Los Angeles dates, pooled across sports, so same-date correlation across sports is handled. Weather: 366 measured dates across cities.
   - **Implied sds:**
     - sports SE 0.0047 × √10,048 ≈ 0.471, against a price-based 0.469. The design effect is about 1.
     - weather 0.0014 × √2,557 ≈ 0.071, against the registered 0.0709.
4. **Power:** recomputed, and every figure is correct.
   - n_for_effect(0.5, 0.02) = ⌈(2.80158 × 25)²⌉ = 4,906 ✓.
   - detectable_effect(0.5, 10,048) = 0.0140 ✓.
   - Weather: 2.80158 × 0.0709 / √2,557 = 0.0039 ✓.
   - The claim-power constants (script:50-53) match prereg.md:259-263 and result.md:37 and :51.
   - The sds come from bounds and prices, not outcomes.
   - No test group has N < 20. Cells with N < 20 are finding 5.
5. **Multiple testing:** no issue found.
   - The primary test stands alone at α 0.05. The family is the registered five, under BH (script:359-362).
   - Recomputed: sorted p values 0, .1358, .2012, .3220, .3860. Only weather passes (rank 1). The adjusted values .3354/.3354/.3860/.3860 match result.md:45-49.
   - The exploratory runs print no p value. The Wilson intervals are labelled "not a test". The harness paired differences are not printed.
6. **Pre-registration integrity:**
   - **Matches:** every rule, threshold, seed, edge and formula.
   - **Recomputed:**
     - B pooled = 0.0087 × 10,183/10,172 + 0.0112 × 124/10,172 ≈ 0.0089 ✓, and Football through Baseball ✓;
     - the claim path: 0.0024 − 0.0089 < 0, and the bootstrap interval excludes 0, so the reason is correctly "widened interval" (script:290-292);
     - the follow-up is "Not met" ✓.
   - **Differences:**
     - (a) No registered consequence is printed for the claimed secondary, and the closing line is broader than section 9 (finding 1).
     - (b) The unregistered zero-count weather exclusion (finding 7).
     - (c) Zero-count rules are not printed.
   - **Dataset id:** `34babdc762e232b0` is the same in result.md:5, prereg.md:75 and dataset_manifest.json:2.
   - **Commit order:** NOT CHECKED (finding 7).
7. **Determinism:** no issue found.
   - The bootstrap is seeded (script:216) over sorted clusters.
   - Sets are sorted before use (:329; harness.py:157, :177). Funnels are printed sorted (:375-378). Dicts are iterated in fixed tuple order.
   - DuckDB runs single-threaded with ORDER BY. There is no wall-clock time or temporary path in the output, and floats use fixed formats.
   - The caller reports two byte-identical runs. That is OBSERVED by the caller, not by me.
8. **Reporting honesty:**
   - Null, non-claimed and claimed results get equal care. "Not a trading result" and "no result here is an edge" are printed (result.md:175-176).
   - **Overclaim risk:** the closing sentence, as applied to weather (finding 1), and the missing reading of what the weather claim means (finding 2).
   - The sports non-claim does not overclaim. If anything it under-explains (finding 3).
9. **Other:**
   - **The harness baselines.** The "Kalshi mid baseline" scores (result.md:158-159, :164-165) equal the source's by construction, because the filter passed on every scored row. They validate nothing.
   - **Fees.** The fee is stated with the multiplier assumed 1, and `config/fee_schedule.json` has no cited value for it. If Kalshi rounds fees up per order (INFERRED, not in repo files), real costs are higher, which only strengthens "cannot be traded".
   - **The harness merge** (validation_log.md:889-891) is gated on this review. The harness itself is not the cause of any finding here.
