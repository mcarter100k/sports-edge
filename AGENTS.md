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

## PROTECTED — owner review before any merge

The evidence path: everything that fetches, stores, compares, logs or grades.

```
sports_edge/kalshi.py      sports_edge/store.py      sports_edge/snapshot.py
sports_edge/fees.py        sports_edge/odds.py       sports_edge/matching.py
sports_edge/shadow.py      sports_edge/ledger.py     sports_edge/grader.py
sports_edge/health.py      sports_edge/config.py     sports_edge/timeutil.py
config/*.json              scripts/*.sh              scripts/launchd/*.plist
```

- **Spec first.** A change to a protected file starts with a spec in
  `docs/specs/`, committed before the code that implements it.
- **The shadow threshold and fee model are part of the evidence.** Changing
  either starts a new `schema_version` / `rule_id` in the shadow ledger;
  rows written under the old rule are never re-labelled.

**Data is evidence — append-only, never edited by hand:**
`data/shadow_ledger.csv`, the `price_snapshots`, `settlements`,
`reference_odds` and `job_runs` tables, and everything under `logs/`.
Nobody edits, regenerates or deletes them. Every CSV append goes through
`sports_edge/ledger.py` (lock, header check, fsync). A wrong row is fixed by
a dated exclusion entry, never by deleting it.

---

## FREE ZONE — build with tests green

- `studies/` — report-only research scripts; never imported by the live path
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
launchd plists: `scripts/launchd/` (installed with `scripts/launchd/install.sh`)
