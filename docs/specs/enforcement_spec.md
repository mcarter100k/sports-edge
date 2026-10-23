# Spec: editor enforcement (`.claude/settings.json`, `.claude/hooks/guard.py`)

Status: Milestone 1, owner-directed 2026-10-22. Not a protected file.

**Why.** AGENTS.md states the rules for evidence and protected files. This
makes the editor refuse the actions those rules forbid, so an editing
session cannot break them by mistake. It is a guard against accidents, not a
security boundary: a program started from the shell (for example
`python -c`) can still write anywhere the user can.

## 1. Permission rules (`.claude/settings.json`, `permissions.deny`)

| Rule | What it stops |
|---|---|
| `Edit(/data/**)` | editing or creating any file under the live `data/` |
| `Edit(/logs/**)` | the same for `logs/` |
| `Edit(/config/fee_schedule.json)` | editing the fee table in the live checkout |
| `Read(/.env)` | reading the credentials file (the Read tool and recognised shell readers such as `cat`) |
| `Bash(git add -A)`, `Bash(git add -A *)`, `Bash(git add --all)`, `Bash(git add --all *)`, `Bash(git add .)`, `Bash(git add . *)` | bulk staging |

- A leading `/` anchors a rule at the project root (the session's primary
  working directory), not the current directory.
- One `Edit(...)` rule covers every file-writing tool, Write included. The
  editor accepts `Write(path)` rules but never matches them, so none are
  written.
- An `Edit` rule also covers shell redirection (`>`, `>>`) and `tee`
  targets.

## 2. The guard hook (`.claude/hooks/guard.py`)

Runs before every Edit, Write, MultiEdit, NotebookEdit and Bash call. It
blocks with exit code 2 and a reason on stderr. Standard library only;
it must run on Python 3.9 and later.

**Which checkout a path belongs to.** Walk up from the path to the first
folder holding `.git`. A checkout counts only if its `AGENTS.md` has the
PROTECTED block. Paths in no such checkout are allowed.

**A feature worktree** is a linked worktree (`.git` is a file) whose `HEAD`
names a branch other than `main` or `master`. The main checkout is never a
feature worktree, whatever branch it is on.

**File tools (Edit, Write, MultiEdit, NotebookEdit).** For the target path
relative to its checkout:
1. Under `data/` or `logs/`: blocked in every checkout. Evidence is never
   edited by hand.
2. On the PROTECTED list: blocked unless the checkout is a feature
   worktree. The list is read from the checkout's own `AGENTS.md`, from the
   first fenced block under the `## PROTECTED` heading (first word of each
   line; shell-style wildcards allowed). There is no second copy of the
   list to fall out of date.
3. Under `.claude/`: blocked unless in a feature worktree, so the guard
   cannot be switched off from the live checkout.

**Bash.** Best effort. The command is split on `;`, `&&`, `||`, `|` and
newlines, and each part is checked:
1. Bulk staging is blocked: `git add` with `-A`, `--all`, `-u`,
   `--update`, `.`, `:/` or `*` among its arguments, including after
   `git -C <dir>` or `git -c <k=v>`. `git commit` with `-a`, `--all` or a
   combined short flag containing `a` (for example `-am`) is blocked, since
   it stages without naming files.
2. Writes to evidence are blocked: a redirection target (`>`, `>>`), a
   `tee` target, or a path argument to `rm`, `mv`, `cp` (destination),
   `truncate`, `dd of=` or `sed -i` that resolves under `data/` or `logs/`
   of a checkout. Relative paths resolve against the hook's `cwd`, updated
   by any `cd <dir>` earlier in the same command.
3. Writes by the same commands to a PROTECTED file outside a feature
   worktree are blocked.
4. If the command cannot be split (unbalanced quotes), a plain pattern scan
   applies rules 1 and 2 to the raw text.
5. **Heredoc bodies are text, not commands** (amended 2026-10-22). Every
   body (`<<WORD` or `<<'WORD'` … a line holding only `WORD`; `<<-`
   allows leading tabs) is removed before the steps above. The line that
   opens the heredoc stays, so `cat > data/x <<EOF` is still blocked by
   its redirection target. Before this amendment, a heredoc that only
   wrote documentation mentioning bulk staging was blocked.

**Fail closed.** Unreadable input, or any error inside the guard, blocks
the call with the error as the reason.

## 3. Tests

- **Gate 1** (`tests/test_guard_hook.py`): every row above against
  temporary git repositories (a main checkout, a feature worktree and a
  detached worktree), including the cases that must be allowed. A pinned
  test checks that the guard's list equals the repo's AGENTS.md block.
- **Gate 2, the real editor:** a headless probe in a scratch repository with
  this settings file. Each rule is tried twice, with the project settings
  and, as a control, without them. A rule passes only if the target file's
  hash is unchanged (or the canary text is absent) with the rule, and
  changes (or appears) without it.

## Known limits

- A script run from the shell (`python`, `node`, `sh -c '…'`) can still
  write files. The guard only reads the command text.
- `Read(/.env)` is best effort for Grep and Glob.
- The deny rules anchor at the session's primary working directory. In a
  worktree session, the hook gives the protection.
