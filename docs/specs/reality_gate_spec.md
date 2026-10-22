# Spec: the reality gate (`scripts/reality_run.py`)

Status: Gate 2 of AGENTS.md rule 3. Not a protected file.

**What it does.** It runs one command with the real code against the real
APIs:
- from a temporary folder, with `SPORTS_EDGE_DATA_DIR`,
  `SPORTS_EDGE_LOG_DIR`, `SPORTS_EDGE_STATE_DIR` and
  `SPORTS_EDGE_SHEET_DIR` inside it;
- with `SPORTS_EDGE_NOTIFY=log`;
- with credentials from the live checkout's `.env`.

It hashes the live data before and after, and scans the output for failure
markers. The exit codes are in the script's docstring.

## Amendment 2026-10-22: `--code` also decides what a wrapper runs (owner-approved)

**The problem (observed 2026-10-22).** `--code DIR` only set `PYTHONPATH`.
The wrappers (`scripts/run_*.sh`) source `common.sh`, which changes into
`/Users/quis/sports-edge` and runs `python -m`. Python puts the current
folder ahead of `PYTHONPATH`, so a wrapper gate ran the live checkout's
code, not `DIR`'s.

**The rule.** When `--code` is not the live checkout, the command also gets
the three settings `common.sh` already reads:
- `SPORTS_EDGE_PROJ=<DIR>`: the wrapper changes into `DIR` and runs its
  code.
- `SPORTS_EDGE_PYTHON=/Users/quis/sports-edge/venv/bin/python`: worktrees
  have no `venv/`.
- `SPORTS_EDGE_NO_ENV_FILE=1`: worktrees have no `.env`. The gate has
  already loaded the live `.env` into the environment.

With `--code` left at the live checkout, nothing changes. A wrapper there
still sources the live `.env` and fails if it is missing.

**Each run report prints** `code path: <DIR> (wrappers run <DIR>)`, so the
output shows which code ran.
