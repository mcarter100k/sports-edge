# AGENTS.md — operating contract for this repo

**What this is:** a read-only research system for Kalshi sports markets. It
records prices, compares them with sharp-book reference odds, and logs where
a fee-adjusted gap would have existed, in a shadow ledger that is graded
after settlement. It never places an order and never authenticates to a
trading endpoint.

**Current phase: PHASE 1 — data foundation + shadow ledger.** Out of scope:
order placement, position sizing, sport-specific outcome models.

**Success metric: zero unreviewed changes to the evidence path.** Nothing is
VALIDATED without honest, independent sample sizes. An unproven gap is a
hypothesis, not an edge.

---

## Team
- /red-team <file or idea>: attacks a design or claimed edge before building or betting
- /research <idea>: pre-registers, writes study code, runs the harness (needs the harness on main)
- /audit <id>: research-auditor re-runs a study in a fresh context and accepts or rejects it; never in the same session as the research
- auditor (read-only): unchanged; still does pre-registration and change reviews
- /lean-review <branch>: staff-engineer check before every merge
- Code, not commands: prices (shadow rule, board, rate), statistics (research/stats.py), orchestration (the owner + research/registry/)
- Flow: idea → /red-team → /research → /audit → owner approves in the registry. Code: branch → /lean-review → owner merges.

---

## PROTECTED — owner review before any merge

Narrowed by the owner on 2026-10-22 to the files that decide what counts as
evidence:

```
sports_edge/ledger.py        the append-only ledger writer
sports_edge/fees.py          the fee model
config/fee_schedule.json     the fee table (every value cited to docs/sources/)
sports_edge/grader.py        the grader
sports_edge/shadow.py        the signal rule and its thresholds
sports_edge/matching.py      odds matching
sports_edge/checks.py        kickoff slots and check times (decide which games cost odds credits)
config/team_aliases.json     team-name aliases (change which games pair)
config/sport_groups.json     sport mapping (changes which games pair)
research/harness.py          scores probability sources (decides what a study measures)
research/registry/*/result.md  registered study results (written by the study script only)
```

Added by the owner on 2026-10-22: the research harness and registered
results. A pre-registration (`research/registry/*/prereg.md`) is committed,
and reviewed under `docs/reviews/`, before its study runs.

Everything else gets normal review: suite green and the reality gate run
before merge.

- **Spec first.** A change to a protected file starts with a spec in
  `docs/specs/`, committed before the code that implements it.
- **The signal rule and the fee model are part of the evidence.** Changing
  either one starts a new `rule_id` in the shadow ledger. Rows written under
  the old rule are never relabelled.

**Data is evidence — append-only, never edited by hand:**
`data/shadow_ledger.csv`, `data/shadow_markouts.csv`, the `price_snapshots`,
`settlements`, `reference_odds` and `job_runs` tables, and everything under
`logs/`.
Nobody edits, regenerates or deletes them. Every CSV append goes through
`sports_edge/ledger.py` (lock, header check, fsync). A wrong row is fixed by
a dated exclusion entry, never by deleting it.
Frozen research datasets (`~/sports-edge-research/datasets/<name>/<id>/`,
outside the repo) are never edited either: new data is a new freeze with a
new id.

---

## FREE ZONE — build with tests green

- `research/studies/` — report-only research scripts; never imported by the live path
- Tests (always welcome)
- Docs (`*.md`)

---

## STANDING RULES

1. **Branch workflow.** `main` is the live checkout: launchd runs whatever
   is checked out at `/Users/quis/sports-edge`, so it stays on `main`.
   Every task starts a feature branch in a worktree outside the repo folder:
   `git -C /Users/quis/sports-edge worktree add -b feat/<name> <dir> main`.
   Merge only when the suite is green, the reality gate has passed, and —
   for protected files — the owner has reviewed. Remove the worktree after.

2. **Git safety.** Stage files explicitly; never `git add -A`. `.env`,
   `data/`, `logs/` and `venv/` are gitignored and must never be staged.
   The parent directory `/Users/quis` is a different repository that
   ignores this folder; never run git commands for this repo from there.

3. **Two-gate verification.**
   - **Gate 1 — controlled tests.** Failure paths, rare conditions, pinned
     values. Required, never sufficient. Nothing merges on Gate 1 alone.
   - **Gate 2 — reality gate (mandatory before every merge).** Run the real
     code against the real Kalshi API (and the real odds source where
     touched) with `scripts/reality_run.py`. It runs the target against a
     temporary data directory and fails if anything in the live data
     directory changed. Every run report carries a **REALITY GATE** section
     showing actual output. Mock output never appears there. An exit code
     is not a certification; read the output.
   - **PENDING** — anything only time can prove (a scheduled job firing, a
     market settling, a weekly ping) is recorded as PENDING in
     `docs/validation_log.md` and cleared only with a date and evidence.

4. **Suite green before any commit.** `venv/bin/python -m pytest -q`.
   `tests/conftest.py` gives every test its own data directory, blocks the
   network (mark real-network tests `@pytest.mark.integration`), and fails
   the session if any file in the repo changed. Never weaken it to make a
   test pass.

5. **Time.** UTC inside the machine: every stored timestamp is UTC ISO-8601
   with `Z`. America/Los_Angeles for anything a person reads (notifications,
   reports, heartbeat), always with the zone shown. Conversion happens in
   `sports_edge/timeutil.py` only.

6. **Never guess a match.** A Kalshi market is compared with a reference
   price only through a match that passes every rule in
   `docs/specs/odds_matching_spec.md`. Anything ambiguous or unmatched is
   logged with the prefix `UNMATCHED` and counted; it is never compared.

7. **Scheduled jobs fail loudly.** Every launchd job sources `.env`, exits
   nonzero on any error, and alerts the owner (see
   `docs/specs/heartbeat_spec.md`). A missing weekly proof-of-life ping is
   itself the alarm.

8. **A zero from an unproven path is not data.** A display or report must
   distinguish "no observations" from "path not shown able to produce one".

9. **Evidence honesty.** Report N as independent events, not rows (two
   sides of one game are one event). Flag N < 20 as INSUFFICIENT. Negative
   results are logged with the same care as positive ones.

10. **No claim without observed evidence.** Label every claim in a run
    report VERIFIED (observed, and the check was shown able to fail),
    OBSERVED, INFERRED, PENDING or NOT CHECKED. Only VERIFIED may be called
    done. If an earlier claim is found to be wrong, lead with the correction.

11. **Report every run.** End with: files touched, diffstat, tests run and
    result, reality gate output, and anything deferred for owner review.

12. **Writing rules.** Plain English in docs and reports. Commits, code,
    comments and docs describe the work only.

---

## Live entry points

Snapshot cycle (every 15 min): `scripts/run_snapshot.sh`
Nightly grader: `scripts/run_grader.sh`
Weekly proof-of-life: `scripts/weekly_heartbeat.sh`
Stale-snapshot watch (every 15 min, at :07/:22/:37/:52): `scripts/run_watch.sh`
Every job module runs under a 2,200 s limit: `sports_edge/jobguard.py`
launchd plists: `scripts/launchd/` (installed with `scripts/launchd/install.sh`)
