# Review: 2026-10-kalshi-calibration, result (re-check of 6e318f7)

- Reviewed commit: `6e318f7d3cf2483054451a83cdba56fe827523c9`
- Reviewer: `.claude/agents/auditor.md` (tools Read, Grep, Glob only), fresh headless session, 2026-10-23T20:53:14Z
- Saved verbatim below; not edited.

---

**VERDICT: APPROVE**

Summary: Commit 6e318f7 answers five of the six findings from my previous review in full: 1, 2, 3, 4 and 5. Finding 6 is only partly answered. A clean rerun is recorded, but it doesn't say who ran it, and the `git log` it was asked to show isn't there. The commit changes only `result_note.md` and `docs/validation_log.md`. Every figure in it agrees with `result.md` and with the newly pasted script output. It overstates nothing in a way that matters: the claims that move are both made more cautious. I found three new minor issues. One is a working-tree hazard I saw during this review: the validation log briefly held merge-conflict markers.

Findings:

1. **[severity: minor] The rerun record lacks the git evidence and the name of whoever ran it.** docs/validation_log.md:983, :986-990
   - **What's wrong:** Line :983 says second-review finding 8 "is answered by the clean rerun below". My previous review assigned that check to you (r3:44-48). The entry at :986-990:
     - doesn't say who ran it;
     - doesn't show the `git log --oneline` I asked for;
     - writes `git diff --stat 1e2e506 HEAD` without naming the commit HEAD was at;
     - quotes only the sha256, not the command or the full `STUDY OK` line.
   - **Why it matters:** You have delegated acceptance. If the author ran it, the claim that the result came first and `result.md` is unchanged still rests on the author's word. I can't read git, so this is NOT CHECKED by me. What I did check: every `result.md` line cited in earlier reviews still holds the same content (:33, :49, :139-144, :170-171).
   - **Fix:** Append one line naming who ran the rerun and the exact command. Paste `git log --oneline 586ca79^..6e318f7` and name the commit HEAD was at for the diff. Or run those two git commands yourself before you accept.

2. **[severity: minor] The positive post-hoc figure lacks the caveat the negative one now has.** result_note.md:102-104 against :99-101
   - **What's wrong:** The pooled after-fee loss (−0.56 cents) now carries "no interval, and the brackets are not independent". The sub-0.30 gain (+0.55 cents per bracket, 10,672 brackets) does not.
   - **What the new log output shows** (validation_log.md:1002-1005): the after-fee gain comes entirely from the buckets under 0.20 (+0.0064, +0.0108, +0.0086). The 0.20–0.30 bucket loses 0.64 cents per bracket after the fee.
   - **My arithmetic:** (4,770 × 0.0064 + 1,934 × 0.0108 + 2,183 × 0.0086 − 1,785 × 0.0064) / 10,672 = +0.0055 ✓.
   - **Why it matters:** This text was already there at 8ee60c7, so 6e318f7 did not introduce it. But rule 9 asks for the same care on both sides. Anyone writing a follow-up pre-registration from this note would start with a slice that includes a losing bucket.
   - **Fix:** Add "(point estimate, no interval, brackets not independent)" to :103-104. Add one clause: "the after-fee gain is in the buckets under 0.20; the 0.20–0.30 bucket loses 0.64 cents per bracket after the fee".

3. **[severity: minor, process] The validation log changed on disk while I was reviewing it.** docs/validation_log.md
   - **What I saw:** My first read showed the calibration section starting at line 932, not 923. It also had `=======` at line 1021 and `>>>>>>> main` at line 1346, wrapping later `main` entries ("Milestone 1, editor enforcement" through "combo alerts"). Later reads and searches showed the clean file: 1,011 lines, section at :923, no markers.
   - **What it suggests (INFERRED):** another session was merging `main` into this worktree, or rebasing it, while I read. That fits the shared-worktree warning.
   - **Why it matters:** This file is the run record. A merge resolution that commits conflict markers, or reorders entries, would damage the evidence trail. The line numbers in this review are from the clean version.
   - **Fix:** Before accepting, confirm the worktree's HEAD is the commit you are accepting. Run `grep -nE '^(<<<<<<<|=======|>>>>>>>)' docs/validation_log.md` on whatever gets merged; it should print nothing.

How each finding from my previous review (r3) was answered:

- **r3 finding 1 (the 0.0065 figure):** Answered. result_note.md:30-33 now says "about 0.0082", which is 0.0065 + 0.0017, and :133 gives the source of the 0.0017. ✓ 0.0011 + 0.0006 = 0.0017. Line :33 is not wrapped (cosmetic only).
- **r3 finding 2 (labels):**
  - **Overlap of the 5 h and 8 h runs:** answered. :42-44 now says INFERRED and "the overlap was not computed". "Very likely" is a fair reading of the counts.
  - **Tick floor:** answered. :88-90 says 46%, "may hold its mid up (INFERRED)", and "nothing here measures how much of the deficit the floor causes". The overstatement is gone.
- **r3 finding 3 (miscount in the run record):** Answered. validation_log.md:977-983 says nine findings, 1–6 at 8ee60c7, and 7 and 9 in the next commit (which is 6e318f7). The correction line has no date of its own, but it sits inside the 2026-10-23 entry. Acceptable.
- **r3 finding 4 (point-estimate wording):** Answered. :99-101 replaces the flat claim with "point estimate after the modelled fee, with no interval… not independent". The table header at :76 now says "(not independent)". The positive slice still lacks the same caveat (finding 2).
- **r3 finding 5 (no printed output):** Answered. validation_log.md:991-1010 gives the command, the commit (8ee60c7) and the actual output. I checked the output against the note:
  - each bucket's n, Σ(y − mid), Σ(y − bid) and bid-0.01 share matches :78-83;
  - Σ(y − mid) adds to −148.4 ✓;
  - the pooled figures, +0.0018 before the fee and −0.0056 after, recompute from the per-bucket lines ✓;
  - the implied fees per bucket, about 0.0013, 0.0039, 0.0081 and 0.0126, fit 0.07 × bid × (1 − bid) at those prices.
- **r3 finding 6 (owner git and rerun check):** Partly answered. A clean rerun from a detached worktree at 586ca79 is recorded, with the matching sha256 `b8f8cd29…`. It doesn't say who ran it, and the git log isn't shown (finding 1). NOT CHECKED by me.

Checks on the commit itself:
- **`result.md` and the registered conclusions:** unchanged. The diff touches only the two files. In the note, the pooled-sports non-claim (:18-24) and the weather "clear but untradeable" consequence (:51-53) read as they did at 8ee60c7.
- **New overstatement:**
  - None in the edited lines. Every edit makes a claim weaker or adds a label.
  - The rerun's label "VERIFIED" is acceptable as a type of check, because a sha256 comparison can fail. Its evidence is incomplete (finding 1).
  - The one remaining imbalance is older text (finding 2).
