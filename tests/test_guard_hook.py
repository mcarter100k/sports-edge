"""The pre-tool guard (docs/specs/enforcement_spec.md), run as the editor runs it:
one JSON call on stdin, exit 2 to block."""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / ".claude" / "hooks" / "guard.py"
SETTINGS = REPO / ".claude" / "settings.json"

AGENTS = """# AGENTS.md

## PROTECTED — owner review before any merge

```
sports_edge/fees.py          the fee model
config/fee_schedule.json     the fee table
research/registry/*/result.md  results
```

Everything else gets normal review.
"""


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def repos(tmp_path_factory):
    """A main checkout on main, a feature worktree and a detached worktree."""
    base = tmp_path_factory.mktemp("guard")
    main = base / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    (main / "AGENTS.md").write_text(AGENTS)
    for rel in ("sports_edge/fees.py", "sports_edge/other.py", "config/fee_schedule.json",
                "research/registry/s1/result.md", "research/registry/s1/prereg.md", ".claude/settings.json"):
        (main / rel).parent.mkdir(parents=True, exist_ok=True)
        (main / rel).write_text("x\n")
    git(main, "add", "AGENTS.md", "sports_edge", "config", "research", ".claude")
    git(main, "commit", "-q", "-m", "init")
    feat = base / "feat"
    git(main, "worktree", "add", "-q", "-b", "feat/x", str(feat), "main")
    detached = base / "detached"
    git(main, "worktree", "add", "-q", "--detach", str(detached), "main")
    on_master = base / "on_master"
    git(main, "worktree", "add", "-q", "-b", "master", str(on_master), "main")
    outside = base / "outside"
    outside.mkdir()
    return {"main": main, "feat": feat, "detached": detached, "on_master": on_master, "outside": outside}


def run(call, python=sys.executable):
    proc = subprocess.run([python, str(GUARD)], input=json.dumps(call) if isinstance(call, dict) else call,
                          capture_output=True, text=True)
    return proc.returncode, proc.stderr


def edit(path, tool="Edit", cwd="/"):
    return run({"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(cwd)})


def bash(command, cwd):
    return run({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


# File tools -------------------------------------------------------------------

@pytest.mark.parametrize("where", ["main", "feat", "detached"])
@pytest.mark.parametrize("rel", ["data/sports_edge.duckdb", "data/new.csv", "logs/alerts.log"])
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_evidence_is_blocked_in_every_checkout(repos, where, rel, tool):
    rc, err = edit(repos[where] / rel, tool)
    assert rc == 2 and "evidence" in err


@pytest.mark.parametrize("rel", ["sports_edge/fees.py", "config/fee_schedule.json",
                                 "research/registry/s1/result.md"])
def test_protected_file_blocked_in_main_and_detached_allowed_in_feature(repos, rel):
    assert edit(repos["main"] / rel)[0] == 2
    assert edit(repos["detached"] / rel)[0] == 2
    assert edit(repos["on_master"] / rel)[0] == 2
    assert edit(repos["feat"] / rel) == (0, "")


def test_unprotected_files_are_allowed_everywhere(repos):
    for where in ("main", "feat", "detached"):
        assert edit(repos[where] / "sports_edge" / "other.py") == (0, "")
        assert edit(repos[where] / "research" / "registry" / "s1" / "prereg.md") == (0, "")
    assert edit(repos["outside"] / "data" / "x.csv") == (0, "")


def test_enforcement_files_change_only_in_a_feature_worktree(repos):
    assert edit(repos["main"] / ".claude" / "settings.json")[0] == 2
    assert edit(repos["feat"] / ".claude" / "settings.json") == (0, "")


def test_relative_path_resolves_against_cwd(repos):
    rc, _ = run({"tool_name": "Write", "tool_input": {"file_path": "data/x.csv"}, "cwd": str(repos["feat"])})
    assert rc == 2


def test_notebook_path_is_checked(repos):
    rc, _ = run({"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(repos["main"] / "logs" / "a.ipynb")},
                 "cwd": "/"})
    assert rc == 2


def test_main_checkout_on_a_feature_branch_is_still_not_a_feature_worktree(tmp_path):
    git(tmp_path, "init", "-q", "-b", "feat/y")
    (tmp_path / "AGENTS.md").write_text(AGENTS)
    assert edit(tmp_path / "sports_edge" / "fees.py")[0] == 2


def test_a_checkout_without_the_protected_block_is_not_guarded(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "AGENTS.md").write_text("# no protected block\n")
    assert edit(tmp_path / "data" / "x.csv") == (0, "")


# Bash -------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git add -A", "git add --all", "git add .", "git add -u", "git add src -A", "git -C /x add -A",
    "git -c core.x=1 add --all", "cd /tmp && git add -A", "git status; git add .", "git add ':/'",
    "git commit -am 'msg'", "git commit -a -m msg", "git commit --all -m msg", "/usr/bin/git add -A",
])
def test_bulk_staging_is_blocked(repos, command):
    rc, err = bash(command, repos["feat"])
    assert rc == 2, command
    assert "AGENTS.md rule 2" in err


@pytest.mark.parametrize("command", [
    "git add sports_edge/other.py tests/test_x.py", "git add -p sports_edge/other.py",
    "git commit -m 'add all the things'", "git commit --amend --no-edit", "git status", "git diff -- .",
])
def test_named_staging_and_other_git_is_allowed(repos, command):
    assert bash(command, repos["feat"]) == (0, ""), command


@pytest.mark.parametrize("command", [
    "echo x > data/f.csv", "echo x >> logs/alerts.log", "echo x >data/f.csv", "printf x | tee data/f",
    "printf x | tee -a logs/a.log", "rm data/sports_edge.duckdb", "rm -f logs/a.log", "mv data/a /tmp/a",
    "cp /tmp/a data/shadow_ledger.csv", "truncate -s 0 data/a", "dd if=/dev/zero of=data/a",
    "sed -i '' 's/a/b/' data/a.csv",
])
def test_evidence_writes_from_bash_are_blocked(repos, command):
    rc, err = bash(command, repos["main"])
    assert rc == 2, command
    assert "evidence" in err


def test_cd_earlier_in_the_command_is_followed(repos):
    assert bash(f"cd {repos['main']} && rm data/x", "/")[0] == 2
    assert bash(f"cd {repos['outside']} && rm data/x", repos["main"]) == (0, "")


@pytest.mark.parametrize("command", [
    "cat data/a.csv > /tmp/copy.csv", "ls logs/", "grep x data/a.csv", "cp data/a.csv /tmp/a.csv",
    "venv/bin/python -m pytest -q 2>&1 | tail -5", "echo hi > /tmp/x",
])
def test_reading_evidence_from_bash_is_allowed(repos, command):
    assert bash(command, repos["main"]) == (0, ""), command


def test_protected_writes_from_bash_follow_the_worktree_rule(repos):
    cmd = "sed -i '' 's/0.07/0.08/' sports_edge/fees.py"
    assert bash(cmd, repos["main"])[0] == 2
    assert bash(cmd, repos["feat"]) == (0, "")


def test_unsplittable_command_falls_back_to_a_pattern_scan(repos):
    # The unbalanced quote makes the command unsplittable.
    assert bash("echo \"it's > data/a.csv", repos["main"])[0] == 2
    assert bash("git add -A; echo 'oops", repos["main"])[0] == 2
    assert bash("echo 'fine", repos["main"]) == (0, "")


def test_heredoc_writing_a_new_file_outside_evidence_is_allowed(repos):
    cmd = "cat > /tmp/notes.txt <<'EOF'\nit's x >= 1 and y -> z\nEOF"
    assert bash(cmd, repos["main"]) == (0, "")


# Fail closed and the real configuration ----------------------------------------

def test_unreadable_input_fails_closed():
    rc, err = run("not json")
    assert rc == 2 and "failing closed" in err


def test_file_tool_without_a_path_is_blocked():
    assert run({"tool_name": "Write", "tool_input": {}, "cwd": "/"})[0] == 2


def test_other_tools_pass():
    assert run({"tool_name": "Read", "tool_input": {"file_path": "/x"}, "cwd": "/"}) == (0, "")


def test_guard_list_is_the_repo_agents_md_block():
    sys.path.insert(0, str(GUARD.parent))
    try:
        import guard
    finally:
        sys.path.pop(0)
    text = (REPO / "AGENTS.md").read_text()
    block = re.search(r"^## PROTECTED.*?^```[^\n]*\n(.*?)^```", text, re.S | re.M).group(1)
    expected = [line.split()[0] for line in block.splitlines() if line.strip()]
    assert guard.protected_list(str(REPO)) == expected
    assert "sports_edge/ledger.py" in expected and "config/fee_schedule.json" in expected


def test_guard_runs_on_python_3_9():
    ast.parse(GUARD.read_text(), feature_version=(3, 9))


def test_settings_deny_rules_and_hook_wiring():
    s = json.loads(SETTINGS.read_text())
    deny = s["permissions"]["deny"]
    for rule in ("Edit(/data/**)", "Edit(/logs/**)", "Edit(/config/fee_schedule.json)", "Read(/.env)",
                 "Bash(git add -A)", "Bash(git add -A *)", "Bash(git add --all)", "Bash(git add . *)"):
        assert rule in deny
    assert not [r for r in deny if r.startswith("Write(")], "Write(path) rules never match; use Edit(path)"
    (entry,) = s["hooks"]["PreToolUse"]
    assert set(entry["matcher"].split("|")) == {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}
    assert entry["hooks"][0]["command"].endswith('/.claude/hooks/guard.py"')


def test_heredoc_bodies_are_text_not_commands(repos):
    doc = "cat > /tmp/notes.md <<'EOF'\nthe editor's rule: never run git add -A\nrm data/x\nEOF\necho done"
    assert bash(doc, repos["main"]) == (0, "")
    py = "python3 - <<'EOF'\nprint('git add -A > data/x')\nEOF"
    assert bash(py, repos["main"]) == (0, "")


def test_the_line_opening_a_heredoc_is_still_checked(repos):
    assert bash("cat > data/x.csv <<EOF\nhello\nEOF", repos["main"])[0] == 2
    assert bash("cat <<'EOF' > logs/a.log\nhello\nEOF", repos["main"])[0] == 2


def test_commands_after_a_heredoc_are_still_checked(repos):
    assert bash("cat > /tmp/a <<EOF\nx\nEOF\ngit add -A", repos["main"])[0] == 2
    assert bash("cat > /tmp/a <<-EOF\n\tx\n\tEOF\nrm data/x", repos["main"])[0] == 2


def test_an_unclosed_heredoc_swallows_the_rest_as_text(repos):
    # bash itself reads to the end as the body, so nothing after the opening line runs
    assert bash("cat > /tmp/a <<EOF\ngit add -A", repos["main"]) == (0, "")
