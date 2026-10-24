# Spec: the status command, and `.env` for every command (owner request 2026-10-22)

Not a protected file. Read-only: nothing here places an order or writes
evidence.

## 1. Every `python -m sports_edge.<module>` command loads `.env` itself

**The problem.** The wrappers source `.env` before running Python. A
command typed in Terminal did not, so `slate board` and the others said
"odds not set up" even though the key was in `.env`.

**The rule.** When Python starts a `python -m sports_edge.<module>`
command, the package loads `.env` before the module runs. It uses the same
file and switches as `scripts/common.sh`:
- **The file:** `$SPORTS_EDGE_PROJ/.env`, or the repo's own `.env` when
  `SPORTS_EDGE_PROJ` is not set.
- **Skipped** when `SPORTS_EDGE_NO_ENV_FILE` is set (tests and worktree
  gates).
- **Lines:** `KEY=value` or `export KEY=value`, with optional quotes.
  Blank lines and `#` comments are ignored.
- **A variable already set in the environment wins.** A value typed on the
  command line (`ODDS_PLAN=paid python -m …`) is never replaced. Under a
  wrapper the values are already set from the same file, so nothing
  changes.
- **A missing file** is not an error for a command; it simply loads
  nothing. The wrappers still fail on a missing `.env`, as before.
- **When it runs:** only while Python is starting a `-m` command. At that
  moment Python sets `sys.argv[0]` to `-m` while it imports the package.
  Imports from tests, scripts or other programs never load `.env`.
  `tests/conftest.py` also sets `SPORTS_EDGE_NO_ENV_FILE` for every test.

## 2. `python -m sports_edge.status`

At most five lines, plain English, no jargon (the same word list as the
sheet). Times are Pacific with `PT`.

```
SPORTS-EDGE · 3:58 PM PT
Running fine · last check 3:45 PM PT
Today: 18 games tracked · 0 checked · 0 picks
Why no picks: no fee table
Odds credits: 0 of 15 used today
```

- **Line 1:** `SPORTS-EDGE · <now>`.
- **Line 2,** the first that applies:
  1. **A job is failing:** some job in `state/health.json` has consecutive
     failures. The one that failed most recently is named:
     `Problem: <what failed> failed at <time>, see logs/<job>_<date>.log`.
     The date is the Pacific date of that failure. The wording per job:
     `snapshot` → "price check", `slate_alert` → "pick alerts",
     `grader` → "nightly grading", `heartbeat` → "weekly check", any other
     → its job name.
  2. **No price check yet:**
     `Problem: no price check has run yet, see logs/`.
  3. **Prices not updating:** the last price check finished more than 2
     hours ago (`slate.NOT_RUNNING_AFTER`):
     `Problem: no price check since <time>, see logs/snapshot_<date>.log`.
  4. **Otherwise:** `Running fine · last check <time of the last price
     check>`.
- **Line 3:** `Today: <N> games tracked · <N> checked · <N> picks`.
  - **Tracked:** the in-scope matched games starting on today's Pacific
    date (the slate's day plan).
  - **Checked:** those of them with a `check_log` row of status `checked`
    today.
  - **Picks:** today's numbered picks (results first logged today; two ways
    to back one team count once).
- **Line 4,** only when picks = 0 and a check came due today for at least
  one game (any `check_log` status). It gives the single main reason, the
  first that applies:
  1. `no fee table` or `fee table invalid`;
  2. `odds not set up`;
  3. if no game was `checked`, the most common status of today's checks:
     `out of odds credits`, `fee not confirmed`, `no sharp price`, or
     `checks not run`;
  4. otherwise `no price gaps of 3¢ or more`.

  "A check came due" counts any status, so the line also explains "0
  checked", for example while there is no fee table.
- **Line 5:** `Odds credits: <used today> of <ODDS_DAILY_CREDITS> used
  today`, counted from `odds_calls` since Pacific midnight.

**Writes nothing.**
- The database is opened read-only (`Store.open(read_only=True)`) after
  waiting for the writer's lock, as other readers do. A read-only
  connection never rewrites the file.
- No reply ingest, no sheet, no sent state, no log.
- **With no database yet:** line 1, then
  `Problem: no price check has run yet, see logs/`.
