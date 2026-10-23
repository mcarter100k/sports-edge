# Review: alias batch 1 (config/team_aliases.json)

- Reviewed commit: `dad1a60de6e2d3e27c25ee76c5d36f2684dd86cf`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T20:53:51Z
- Saved verbatim below; not edited.

---

VERDICT: REVISE

Summary: The six aliases look right, and each one is limited to its own sport tag. They can only add pairings, and the uniqueness rules still refuse anything ambiguous. But three things block the merge. First, the tests cannot catch a wrong NFL mapping: if Giants and Jets were swapped, every test would still pass. Second, the branch conflicts with `main`, and parts of the amendment are out of date there. Third, the four NFL aliases were added without the UNMATCHED line the spec requires before any alias is added.

Findings:

1. [severity: major] tests/test_alias_config.py:13-19, 22-24. **The tests cannot detect a wrong NFL mapping.**
   - **Test 1 can only fail if the alias is unnecessary.** It checks `names_match(k, table[k], table)`. That is true by construction for any entry, whatever reference name it points to. So it cannot catch a wrong target. Only its second half has teeth: it fails if the alias is not needed.
   - **Test 2 only checks Los Angeles across sports.** **Test 3 pins only the NBA pair.**
   - **What gets through:** if `"New York G"` and `"New York J"` were swapped, or `"Los Angeles C"` and `"Los Angeles R"` in Football, all three tests pass.
   - **Why it matters:** a swapped alias still pairs the game. It sets `ref_outcome` (matching.py:149) to the wrong team, so the Giants market would be compared with the Jets' fair price. That puts wrong rows into the ledger, and they look valid.
   - **Fix:**
     - Pin the expected table outright: assert that `ALIASES` equals the literal dict from the spec table.
     - Add a same-city game test for Chargers–Rams and for Giants–Jets, each with home and away in both orders, asserting the exact mapping.

2. [severity: major] docs/specs/odds_matching_spec.md:300-301 and :262-266. **The branch is out of date with `main` and the amendment text would be wrong after the merge.**
   - **What I saw:** partway through this review, the worktree briefly held a merge from `main`, apparently started and then undone by another process. During that time:
     - the spec had conflict markers at lines 221, 303 and 400, against a `main` amendment "Kalshi placeholder times (rule version `match_v3`)";
     - `sports_edge/matching.py:12` read `RULE_VERSION = "match_v3"`.
   - **What is stale against `main`:**
     - "Rule version: unchanged, `match_v2`" is false there.
     - "The fix belongs elsewhere … proposed separately" (line 264) describes a change that `main` has already made.
     - The NCAAF row of the coverage table (31 name_mismatch) shows the state before that change.
   - **Fix:** rebase onto `main` and resolve the conflict. Change the rule-version line to "unchanged, `match_v3`". Rewrite or date the college football paragraph so it points to the placeholder amendment. Re-run the suite on the rebased branch. The rebased spec needs a quick re-read before merge.

3. [severity: major] docs/specs/odds_matching_spec.md:90-92 and config/team_aliases.json:2. **The NFL aliases skip the spec's own rule for adding an alias.**
   - **The rule:** both the spec and the `_doc` line say every entry "is added by hand after an `UNMATCHED name_mismatch` line has been inspected".
   - **What happened:** the amendment's own coverage table shows KXNFLGAME with 0 never-matched games (line 235). So none of the four Football entries came from an UNMATCHED line. They came from titles seen in the `markets` table (line 268-270).
   - **The amendment then states a different rule as if it were already written:** line 298, "They wait for an observed listing, per the rule above". The written rule says no such thing.
   - **Why it matters:** the evidence used may well be good enough. But this file is protected precisely because it decides which games pair, and the amendment loosens the entry rule without saying so.
   - **Fix:** either
     - amend the rule explicitly: "an alias may be added once the Kalshi title is observed in a game series (cite an event ticker) and the reference name is observed in `/events` (cite date and sport key)"; or
     - drop the Football entries until an UNMATCHED line appears.

4. [severity: minor] docs/specs/odds_matching_spec.md:275-278, 281-283. **The NFL evidence cannot be traced.**
   - The NBA rows cite a ticker (`KXNBAGAME-26OCT23LACLAL`). The NFL rows only say "KXNFLGAME", so none of the four titles can be traced to a specific event.
   - The reference-name observation (`/events`, 2026-10-22) left nothing saved: `reference_events` stores only matched games, so it holds no Giants or Jets row.
   - Claims are not labelled VERIFIED / OBSERVED / INFERRED, which AGENTS.md rule 10 requires.
   - **Fix:** cite one event ticker per NFL title, cite the `/events` event ids, and label each claim OBSERVED.

5. [severity: minor] docs/specs/odds_matching_spec.md:224-237. **The coverage table cannot be reproduced.**
   - It cites `python -m research.studies.match_coverage`, but no such module exists in this branch. Searching the repo for `match_coverage` finds only the spec.
   - The run time is given as "8:5x PM PT".
   - So I cannot check that "Matched games" counts events rather than market rows, although the column header claims games.
   - **Fix:** commit the script, or drop the table to a sentence that cites the one UNMATCHED line. Give the exact run time.

6. [severity: minor] sports_edge/matching.py:112-114. **An overlap between alias tables is now possible.**
   - The aliases for each of a game's tags are merged with `dict.update`, so if two tables share a key, the last tag wins silently.
   - Before this batch no two tag tables shared a key. Now `"Los Angeles C"` is in both Basketball and Football.
   - It can only bite if one Kalshi series carries both tags, which is unlikely. But it would pick a team silently instead of refusing.
   - **Fix:** a test that fails if any game's tags select tables with conflicting keys, or a refusal in `match()`. The second needs a spec change, because `matching.py` is protected.

7. [severity: minor] tests/test_alias_config.py:22-33. **The tests bypass the real code path.**
   - They call `names_match` and `candidates` with a table picked by hand. So the real tag selection in `match()` (matching.py:110-117) is never run against the real alias file.
   - Test 3 covers only one home/away order.
   - **Fix:** run a Basketball Kalshi game `["Los Angeles C", …]` through `match()` with the real aliases against a Football stub `Los Angeles Chargers`, and assert no match. Add the reversed-order LA game.

8. [severity: minor] Process. **The files changed under me during the review.**
   - My first reads of `matching.py` and the spec showed a merge in progress. Later reads showed the branch state that matches the diff I was given.
   - This review covers the branch state in the diff (commit `dad1a60`).
   - Whoever merges should confirm the worktree is clean and that what gets committed is what was reviewed.

Checklist:

1. **Correctness**
   - **NBA:** `Los Angeles C` → Clippers and `Los Angeles L` → Lakers are correct. They are backed by the ticker `…LACLAL` and the NBA UNMATCHED name_mismatch row (spec:233, 274-275).
   - **NFL:** Chargers, Rams, Giants and Jets are the natural reading, and the reference names follow The Odds API's naming. But the Kalshi titles are not cited to any event (finding 4), and no UNMATCHED line preceded them (finding 3). I did not check them against the live `markets` table (NOT CHECKED: `data/` is not in this worktree).
   - **Guessing:** nothing looks guessed, but the NFL entries are less well documented than the rule requires.

2. **Collisions**
   - **Within a sport:** aliases are scoped through `aliases_by_tag.get(tag)` (matching.py:112-114, odds_step.py:135-136, 158). Basketball `Los Angeles C` cannot reach the Chargers, nor Football `Los Angeles C` the Clippers.
   - **Within a game:** in an LA–LA or NY–NY game, each title matches exactly one side. For example, `los angeles lakers` does not start with `los angeles c ` (with the space), so the mapping is unique (matching.py:45, 80-85).
   - **Other leagues under the same tag:** the Football tag also covers NCAAF and the Basketball tag covers WNBA and college. Keys are exact Kalshi titles, so other leagues are reached only if they use the identical title, which I found no sign of.
   - **Remaining risk:** the overwrite between tag tables (finding 6).

3. **Rule 6**
   - Aliases are only consulted after the normal name test fails (matching.py:47), so they can only add a pairing between the exact two names.
   - Every other rule still applies: time window (93), each team matching exactly one distinct reference team (81-85), no more than one candidate (135), and no reference game claimed by two Kalshi events (141). Each refusal is logged as UNMATCHED `ambiguous`, `name_mismatch` or `no_candidate`.
   - The aliases cannot pair a game that would otherwise be refused for time or uniqueness.

4. **Evidence in the spec**
   - The coverage table says "games", but the script that produced it is not in the repo (finding 5).
   - None of the claims carry VERIFIED/OBSERVED/INFERRED labels.
   - The college football correction is led with honestly (spec:246-252), but it is out of date against `main` (finding 2).

5. **Tests**
   - Only test 3 (the NBA mapping) and test 2 (cross-sport for LA only) can fail on a wrong target. Test 1 can only fail if an alias is unnecessary.
   - **Untested:**
     - the NFL mappings, so a swap goes undetected (finding 1);
     - same-city NFL games, in either order;
     - the reversed order of the LA game;
     - the real `match()` path with the real alias file (finding 7).

6. **Scope**
   - The diff touches only `config/team_aliases.json`, the spec amendment and the new test file.
   - Nothing in the signal rule, fees, ledger, grader or `matching.py` changed in this branch.
   - The rule version correctly stays unchanged, but the value it names must become `match_v3` after the rebase (finding 2).
