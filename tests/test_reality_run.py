"""The reality gate runs the --code checkout, wrappers included (docs/specs/reality_gate_spec.md)."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("reality_run", REPO / "scripts" / "reality_run.py")
reality_run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reality_run)


def test_another_checkout_is_what_the_wrappers_run(tmp_path):
    env = reality_run.code_env(tmp_path)
    assert env == {"SPORTS_EDGE_PROJ": str(tmp_path),
                   "SPORTS_EDGE_PYTHON": str(reality_run.LIVE / "venv" / "bin" / "python"),
                   "SPORTS_EDGE_NO_ENV_FILE": "1"}


def test_the_live_checkout_changes_nothing():
    assert reality_run.code_env(reality_run.LIVE) == {}


def test_common_sh_reads_exactly_these_settings():
    common = (REPO / "scripts" / "common.sh").read_text()
    assert all(f"${{{k}:-" in common or f"${{{k}:-}}" in common for k in reality_run.code_env(Path("/x")))


def test_the_command_gets_the_checkout_and_the_temporary_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(reality_run, "LIVE", tmp_path / "live")       # a stand-in: no real .env is read
    env = reality_run.run_env(tmp_path / "worktree", tmp_path / "tmp")
    assert env["SPORTS_EDGE_PROJ"] == env["PYTHONPATH"] == str(tmp_path / "worktree")
    assert env["SPORTS_EDGE_DATA_DIR"] == str(tmp_path / "tmp" / "data") and env["SPORTS_EDGE_NOTIFY"] == "log"
    assert "SPORTS_EDGE_PROJ" not in reality_run.run_env(tmp_path / "live", tmp_path / "tmp")
