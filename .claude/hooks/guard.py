#!/usr/bin/env python3
"""Pre-tool guard (docs/specs/enforcement_spec.md).

Reads one tool call as JSON on stdin. Exit 0 allows it; exit 2 blocks it,
with the reason on stderr. Standard library only, Python 3.9+.
"""
import fnmatch
import json
import os
import re
import shlex
import sys

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
EVIDENCE_DIRS = ("data", "logs")
MAIN_BRANCHES = {"main", "master"}
BULK_ADD = {"-A", "--all", "-u", "--update", ".", ":/", "*"}
SEPARATORS = {";", "&&", "||", "|", "|&", "&", "\n"}


class Blocked(Exception):
    pass


# Checkouts --------------------------------------------------------------------

def checkout_of(path):
    """(root, git_entry) of the nearest folder holding .git, or (None, None)."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    while True:
        entry = os.path.join(d, ".git")
        if os.path.exists(entry):
            return d, entry
        parent = os.path.dirname(d)
        if parent == d:
            return None, None
        d = parent


def protected_list(root):
    """Patterns from the first fenced block under '## PROTECTED' in AGENTS.md, or None."""
    try:
        with open(os.path.join(root, "AGENTS.md")) as fh:
            text = fh.read()
    except OSError:
        return None
    m = re.search(r"^## PROTECTED.*?^```[^\n]*\n(.*?)^```", text, re.S | re.M)
    if not m:
        return None
    return [line.split()[0] for line in m.group(1).splitlines() if line.strip()]


def feature_worktree(git_entry):
    """True only for a linked worktree whose HEAD names a branch other than main/master."""
    if not os.path.isfile(git_entry):
        return False
    with open(git_entry) as fh:
        first = fh.read().strip()
    if not first.startswith("gitdir:"):
        return False
    gitdir = first[len("gitdir:"):].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(os.path.dirname(git_entry), gitdir)
    try:
        with open(os.path.join(gitdir, "HEAD")) as fh:
            head = fh.read().strip()
    except OSError:
        return False
    if not head.startswith("ref: refs/heads/"):
        return False
    return head[len("ref: refs/heads/"):] not in MAIN_BRANCHES


def check_path(path, verb):
    """Raise Blocked if writing `path` breaks a rule."""
    path = os.path.realpath(path)
    root, git_entry = checkout_of(path)
    if root is None:
        return
    patterns = protected_list(root)
    if patterns is None:
        return
    rel = os.path.relpath(path, os.path.realpath(root))
    first = rel.split(os.sep, 1)[0]
    if first in EVIDENCE_DIRS:
        raise Blocked(f"{verb} {rel}: {first}/ is evidence and is never edited by hand (AGENTS.md)")
    in_feature = feature_worktree(git_entry)
    if any(fnmatch.fnmatch(rel, p) for p in patterns) and not in_feature:
        raise Blocked(f"{verb} {rel}: protected file; change it only in a feature worktree "
                      f"(AGENTS.md rule 1), then owner review")
    if first == ".claude" and not in_feature:
        raise Blocked(f"{verb} {rel}: editor enforcement files change only in a feature worktree")


# Bash -------------------------------------------------------------------------

def split_commands(command):
    """Token lists per simple command. Raises ValueError if the text cannot be split."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    parts, current = [], []
    for tok in lexer:
        if tok in SEPARATORS or (tok and set(tok) <= set(";&|\n")):
            if current:
                parts.append(current)
            current = []
            continue
        if "\n" in tok:
            pieces = tok.split("\n")
            for i, piece in enumerate(pieces):
                if piece:
                    current.append(piece)
                if i < len(pieces) - 1 and current:
                    parts.append(current)
                    current = []
            continue
        current.append(tok)
    if current:
        parts.append(current)
    return parts


def git_subcommand(tokens):
    """(subcommand, args) for a git command, skipping -C <dir> and -c <k=v>; else (None, None)."""
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None, None
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t, tokens[i + 1:]
    return None, None


def check_git(tokens):
    sub, args = git_subcommand(tokens)
    if sub == "add" and any(a in BULK_ADD for a in args):
        raise Blocked("git add: stage files by name; bulk staging is not allowed (AGENTS.md rule 2)")
    if sub == "commit":
        for a in args:
            if a == "--all" or (a.startswith("-") and not a.startswith("--") and "a" in a[1:]
                                and not a.startswith("-m")):
                raise Blocked("git commit -a stages without naming files (AGENTS.md rule 2)")


def write_targets(tokens):
    """Paths this simple command would write, as given on the command line."""
    targets = []
    for i, t in enumerate(tokens):
        if t in (">", ">>", ">|", "&>", "1>", "2>") and i + 1 < len(tokens):
            targets.append(tokens[i + 1])
        elif re.match(r"^(\d?>>?|&>)(?!&)(.+)$", t):
            targets.append(re.match(r"^(\d?>>?|&>)(.+)$", t).group(2))
    if not tokens:
        return targets
    cmd = os.path.basename(tokens[0])
    args = [a for a in tokens[1:] if not a.startswith("-")]
    if cmd in ("rm", "truncate", "tee", "unlink", "shred"):
        targets += args
    elif cmd == "mv":
        targets += args          # moving evidence away changes it too
    elif cmd in ("cp", "ln", "rsync", "install") and args:
        targets.append(args[-1])
    elif cmd == "dd":
        targets += [a[3:] for a in tokens[1:] if a.startswith("of=")]
    elif cmd == "sed" and any(a.startswith("-i") or a.startswith("--in-place") for a in tokens[1:]):
        targets += args          # the script is included too; it never resolves to a protected path
    return [t for t in targets if t and not t.startswith("&")]


HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(command):
    """The command without heredoc bodies: they are text fed to a program, not commands. The line
    that opens each heredoc stays, so its redirection targets are still checked."""
    lines = command.split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            word, tabs = m.group(3), m.group(1) == "-"
            while i < len(lines) and (lines[i].lstrip("\t") if tabs else lines[i]) != word:
                i += 1
            i += 1                      # the closing delimiter line
    return "\n".join(out)


def check_bash(command, cwd):
    command = strip_heredocs(command)
    try:
        parts = split_commands(command)
    except ValueError:
        return check_bash_raw(command, cwd)
    here = cwd
    for tokens in parts:
        if tokens and tokens[0] == "cd" and len(tokens) > 1:
            here = os.path.join(here, os.path.expanduser(tokens[1]))
            continue
        check_git(tokens)
        for target in write_targets(tokens):
            check_path(os.path.join(here, os.path.expanduser(target)), "write")


def check_bash_raw(command, cwd):
    """Fallback when the command cannot be split: pattern scan for bulk staging and evidence writes."""
    if re.search(r"\bgit\b[^;&|\n]*\badd\b[^;&|\n]*(\s-A\b|\s--all\b|\s-u\b|\s\.(\s|$)|\s:/)", command):
        raise Blocked("git add: stage files by name; bulk staging is not allowed (AGENTS.md rule 2)")
    for m in re.finditer(r"(?:>>?|\btee\s+(?:-a\s+)?)\s*['\"]?([^\s'\";&|<>]+)", command):
        check_path(os.path.join(cwd, os.path.expanduser(m.group(1))), "write")


# Entry ------------------------------------------------------------------------

def decide(call):
    tool = call.get("tool_name")
    tool_input = call.get("tool_input") or {}
    cwd = call.get("cwd") or os.getcwd()
    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not path:
            raise Blocked(f"{tool}: no file path in the call")
        check_path(os.path.join(cwd, path), tool)
    elif tool == "Bash":
        check_bash(tool_input.get("command") or "", cwd)


def main():
    try:
        decide(json.loads(sys.stdin.read()))
    except Blocked as exc:
        print(f"BLOCKED by guard: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:          # fail closed
        print(f"BLOCKED by guard (internal error, failing closed): {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
