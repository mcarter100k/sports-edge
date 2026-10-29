# Review: alias batch 1 (config/team_aliases.json), re-review

- Reviewed commit: `70a1b3885da770a17de1e76fa262aca8a092d553`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T21:00:12Z
- Saved verbatim below; not edited.

---

VERDICT: APPROVE

Summary: The revised batch adds only the two NBA Los Angeles aliases. Both rest on a logged `UNMATCHED name_mismatch` game, so the spec's entry rule is met. The new tests would catch a swapped, missing, extra or retargeted entry, and the spec no longer contradicts `main` (`match_v3`). Nothing below blocks the merge. Two conditions remain before merging: confirm the suite is green on the merged branch, and run the reality gate. I could not check either.

Findings:

1. [severity: minor] tests/test_alias_config.py:66-68 — **The second half of the cross-sport test can never fail.**
   - The game's only tag is `Basketball`, so a pool keyed `Football` gives `match()` an empty pool. The game is then skipped (matching.py:176-177), and `r.matches == []` holds whatever the alias file or the pool logic says.
   - Even if pool scoping broke, the stub would still be refused, for the same reason as the first half (line 64).
   - So "a Football reference game is never in a Basketball game's pool" is asserted but not tested.
   - The author's mutation "adding Football `Los Angeles C` → Chargers fails the cross-tag test" is true only of `test_no_two_sport_tags_share_an_alias_key_with_different_targets` (line 34). `test_a_basketball_title_never_reaches_a_football_team` still passes under that mutation, because a Basketball-only game never loads Football aliases.
   - Tag scoping is already tested at tests/test_devig_and_matching.py:124-129, so nothing is uncovered.
   - **Fix:** delete lines 66-68, or replace them with something that can fail. For example, assert `r.unmatched == []` to show the game was skipped, not refused.

2. [severity: minor] docs/specs/odds_matching_spec.md:372-374 — **The amendment may understate how many games these aliases affect.**
   - It says that without the aliases "a game between the two Los Angeles NBA teams could never pair."
   - The NHL evidence suggests Kalshi uses the city-plus-letter title in all of a team's games, not only the derby. The NHL amendment saw `New York R` in an ordinary game (spec:185-188), and the fixture at tests/test_devig_and_matching.py:235 pairs `New York R` with Toronto.
   - If that holds for the NBA, these two entries change pairing for every Lakers and Clippers game, about 160 a season. That is INFERRED. I could not check the `markets` table (`data/` is outside this worktree).
   - The mapping is exact, so the wider effect is correct pairings, not risk. But the owner should know the real scope.
   - **Fix:** one sentence saying whether Kalshi uses `Los Angeles L` / `Los Angeles C` in non-derby games, with a ticker if so.

3. [severity: minor] docs/specs/odds_matching_spec.md:352 vs :221 — **Status labels disagree.**
   - The alias amendment says the placeholder amendment was "merged 2026-10-23".
   - The placeholder amendment's own heading still says "PROPOSED, owner review".
   - The alias amendment's heading (line 318) says "PROPOSED, owner review". Under the owner's delegation, this review is the check.
   - **Fix:** update both headings to their real status when this merges.

4. [severity: minor] docs/specs/odds_matching_spec.md:329-348 — **The coverage table is honest but cannot be reproduced.**
   - The script now exists (research/studies/match_coverage.py), and it counts distinct `event_ticker`s (lines 28-31, 47), so "games" is accurate. Every total and rate in the table checks out:
     - matched games: 39;
     - never matched: 32;
     - overall rate: 39/71 = 55%;
     - NBA: 13/14 = 93%;
     - NCAAF: 8/39 = 21%;
     - games in series that never paired: 124 + 79 + 75 + 2 = 280.
   - But the script has no as-of cutoff, and `odds_matches` and `unmatched` keep growing. A rerun cannot give these numbers, and the output was not saved.
   - "Most are leagues outside `config/slate_sports.json`" (line 347) carries no VERIFIED / OBSERVED / INFERRED label.
   - The NBA `UNMATCHED` line that justifies the batch is not cited by date or day-log file.
   - **Fix:** label line 347 INFERRED, and cite the date of the `UNMATCHED KXNBAGAME-26OCT23LACLAL` line. An `--as-of` option for the script can wait.

5. [severity: minor] Process (AGENTS.md rule 3, rules 1 and 4) — **Merge conditions I could not see evidence for.**
   - No reality-gate output was supplied. This file changes which live games pair, so `scripts/reality_run.py` must run before merge, and its output must be read.
   - I could not run the suite on the merged branch.
   - I could not read git metadata (permission denied on `/Users/quis/sports-edge/.git/worktrees/...`). So I could not confirm that no merge is in progress, or that the spec commit came before the config commit.
   - A search for conflict markers across `*.md`, `*.py` and `*.json` in the worktree found none. The four changed files match the diff I was given.

Earlier findings (docs/reviews/2026-10-23_alias_batch_1.md):
1. Tests cannot detect a wrong NFL mapping — **answered.**
   - The NFL entries are gone.
   - tests/test_alias_config.py:19-24 pins the whole table. A swap, removal, addition or retarget fails there.
   - Lines 49-58 check the exact market → `ref_outcome` mapping through `match()`, in both home/away orders.
2. Branch out of date with `main` — **answered.**
   - matching.py:12 is `match_v3`.
   - Spec line 409 says "unchanged, `match_v3`".
   - The college football paragraph (lines 350-353) points to the placeholder amendment.
   - No conflict markers found.
   - Suite on the merged branch: NOT CHECKED (finding 5).
3. NFL aliases skip the entry rule — **answered.** They are dropped (lines 320-325, 381-383). The `_doc` line and spec:90-92 are unchanged and now consistent with the file.
4. NFL evidence untraceable — **answered.** The point is moot now the entries are dropped, but each title now cites a ticker (lines 363-366) and the claims carry OBSERVED labels. The leftover gaps are in finding 4.
5. Coverage table not reproducible — **partly answered.** The script exists and counts games. The run time is honestly given as "not recorded". The numbers still cannot be regenerated (finding 4).
6. Overlap between alias tables — **answered.** The test at lines 34-41 fails on any shared key with different targets. The spec (lines 385-391) requires a `matching.py` spec change before such an entry is added. That is stricter than the real risk, which is a game with both tags, but it is safe.
7. Tests bypass the real path — **answered.** They call `match()` with the real file, in both orders, plus a cross-sport case. One leftover gap: the second cross-sport assertion can never fail (finding 1).
8. Files changed during review — **partly answered.** The worktree was stable through this review and matches the diff. Git state not readable (finding 5).

Checklist:

1. **Correctness**
   - `Los Angeles C` → `Los Angeles Clippers` and `Los Angeles L` → `Los Angeles Lakers` match the event ticker `KXNBAGAME-26OCT23LACLAL` and the NBA `name_mismatch` count of 1 (spec:340, 361-362).
   - The reference names are OBSERVED by the author in `/events` on 2026-10-22, not saved (spec:368-371). I did not check them against the live API (NOT CHECKED).
   - If a reference name were wrong (for example "LA Clippers"), the alias would fail safe. `names_match` needs an exact normalised equality (matching.py:54), so the game would stay `name_mismatch`, never paired wrongly.
   - The Hockey entries are unchanged.

2. **Collisions**
   - **Across sports:** aliases are scoped by tag (matching.py:173-175, 155-159). The live loader strips `_doc` (odds_step.py:139-140). The test passes `_doc` through, which is harmless because no tag is named `_doc`.
   - **Within the LA game:** `los angeles clippers` does not start with `los angeles l `, and `los angeles lakers` does not start with `los angeles c ` (matching.py:52). So each title hits exactly one side, and `_team_mapping` requires distinct targets (lines 114-119).
   - **Between tables:** no two tag tables share a key, and a test now enforces it (test lines 34-41).
   - **Other leagues under Basketball** (WNBA, college): affected only by the identical title `Los Angeles C` / `Los Angeles L`. I found no sign of that in tests or spec. Not checked against `markets`.

3. **Rule 6 / fail-safe**
   - Aliases are consulted only after the prefix test fails (matching.py:52-54), so they can only add the exact pair.
   - Every other rule still applies:
     - the time window, including the widened placeholder window (lines 129-152);
     - one candidate only (196-200);
     - no reference game claimed by two Kalshi events (202-206).
   - Refusals are still logged `UNMATCHED`.

4. **Evidence in the spec**
   - The coverage arithmetic checks out (finding 4).
   - The NFL and MLB titles are recorded as not in this batch, with the reason given.
   - One claim is unlabelled (line 347). The scope of the aliases' effect may be understated (finding 2). One status label disagrees (finding 3).

5. **Tests**
   - I checked each of the author's mutations against the code:
     - **Swapping C/L:** fails the pinned table (line 21) and the `match()` test (line 54), because the wrong `ref_outcome` comes back.
     - **Removing an entry:** fails the pinned table, and `match()` returns `name_mismatch`.
     - **Retargeting Basketball C to Chargers:** fails the pinned table, the LA test (no hit on the Clippers) and line 65 (the Chargers–Lakers stub now pairs). That makes three.
     - **Adding an unnecessary alias:** fails line 31 and the pinned table.
     - **Adding Football C → Chargers:** fails the pinned table and the shared-key test, not the cross-sport test (finding 1).
   - Line 30 is true by construction, as before. Its real check is line 31.

6. **Scope**
   - The diff touches `config/team_aliases.json`, the spec amendment and the new test file. `matching.py`, fees, ledger, grader and the signal rule are not changed by this branch.
   - The rule version correctly stays at `match_v3`, since aliases are configuration (spec:409-410, as in the NHL amendment).
   - Reality gate and git ordering: NOT CHECKED (finding 5).
