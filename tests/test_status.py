"""The status command and .env for every command (docs/specs/status_spec.md)."""
import hashlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from helpers import fee_table
from sports_edge import config, health, status
from sports_edge.store import Store
from test_slate import CHECK_AT, DAILY_AT, no_jargon, run_at, world

REPO = Path(__file__).resolve().parent.parent
NOW = CHECK_AT + timedelta(minutes=2)            # 7:37 AM PT


def lines_at(now=NOW):
    out = []
    status.run_status(now, out.append)
    assert len(out) <= 5 and no_jargon("\n".join(out).replace("SPORTS-EDGE", "")) == []    # the owner's header
    return out


def day(tmp_path, cheap=True, fee=True, pages=None, check=True):
    if fee:
        fee_table(tmp_path)
    sers, default = world(cheap)
    run_at(DAILY_AT, sers, pages or default)
    if check:
        run_at(CHECK_AT, sers, pages or default)
    health.record_success("snapshot", CHECK_AT)


# .env for every command ------------------------------------------------------------------

def command(tmp_path, *args, **env):
    full = {k: v for k, v in os.environ.items() if k != "SPORTS_EDGE_NO_ENV_FILE"}
    full.update({"SPORTS_EDGE_PROJ": str(tmp_path / "proj"), "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1",
                 **env})
    return subprocess.run([sys.executable, *args], env=full, capture_output=True, text=True, timeout=60,
                          cwd=tmp_path).stdout


@pytest.fixture
def env_file(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / ".env").write_text('# comment\nexport ODDS_DAILY_CREDITS="7"\nODDS_PLAN=paid\n')
    with Store.open():
        pass


def test_a_command_loads_env_and_a_value_already_set_wins(env_file, tmp_path):
    assert command(tmp_path, "-m", "sports_edge.status").splitlines()[-1] == "Odds credits: 0 of 7 used today"
    assert command(tmp_path, "-m", "sports_edge.status", ODDS_DAILY_CREDITS="9").splitlines()[-1].endswith("of 9 used today")
    assert command(tmp_path, "-m", "sports_edge.status", SPORTS_EDGE_NO_ENV_FILE="1").splitlines()[-1].endswith(
        "of 15 used today")


def test_importing_the_package_never_loads_env(env_file, tmp_path):
    out = command(tmp_path, "-c", "import os, sports_edge.status; print(os.environ.get('ODDS_DAILY_CREDITS'))")
    assert out.strip() == "None"


def test_the_loader_reads_the_wrappers_format(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("# c\n\nexport A_ONE='x y'\nA_TWO=\"2\"\nA_THREE=3\nnot a line\n")
    monkeypatch.delenv("SPORTS_EDGE_NO_ENV_FILE")
    monkeypatch.setenv("A_THREE", "kept")
    try:
        assert config.load_env_file(path) == ["A_ONE", "A_TWO"]
        assert (os.environ["A_ONE"], os.environ["A_TWO"], os.environ["A_THREE"]) == ("x y", "2", "kept")
        assert config.load_env_file(tmp_path / "missing.env") == []
    finally:
        for k in ("A_ONE", "A_TWO"):
            os.environ.pop(k, None)


# The five lines -----------------------------------------------------------------------------

def test_a_day_with_a_pick(tmp_path):
    day(tmp_path)
    with Store.open() as s:     # an odds call yesterday never counts toward today
        s.append("odds_calls", [{"ts_utc": DAILY_AT - timedelta(days=1), "sport_key": "x", "endpoint": "odds",
                                 "http_status": 200, "credits_last": 5}])
    assert lines_at() == ["SPORTS-EDGE · 7:37 AM PT", "Running fine · last check 7:35 AM PT",
                          "Today: 1 games tracked · 1 checked · 1 picks", "Odds credits: 1 of 15 used today"]


def test_two_ways_to_back_one_team_are_one_pick(tmp_path):
    sers, pages = world()
    pages[0][0]["markets"][1].update({"no_ask_dollars": "0.1900", "no_bid_dollars": "0.1800"})
    day(tmp_path, pages=pages)
    assert lines_at()[2] == "Today: 1 games tracked · 1 checked · 1 picks"


@pytest.mark.parametrize("setup,why", [
    (dict(fee=False), "no fee table"),
    (dict(cheap=False), "no price gaps of 3¢ or more"),
    (dict(credits="0"), "out of odds credits"),
])
def test_no_picks_gives_one_reason(tmp_path, monkeypatch, setup, why):
    monkeypatch.setenv("ODDS_API_KEY", "test-key")
    if "credits" in setup:
        monkeypatch.setenv("ODDS_DAILY_CREDITS", setup.pop("credits"))
    day(tmp_path, **setup)
    out = lines_at()
    assert out[3] == f"Why no picks: {why}" and len(out) == 5
    assert out[2] == ("Today: 1 games tracked · 1 checked · 0 picks" if why.startswith("no price")
                      else "Today: 1 games tracked · 0 checked · 0 picks")


def test_no_reason_line_before_any_check_comes_due(tmp_path):
    day(tmp_path, check=False)
    assert lines_at(DAILY_AT + timedelta(minutes=5))[2:] == ["Today: 1 games tracked · 0 checked · 0 picks",
                                                             "Odds credits: 0 of 15 used today"]


def test_a_failing_job_is_named_with_its_log(tmp_path):
    day(tmp_path)
    health.record_failure("snapshot", "boom", CHECK_AT + timedelta(minutes=1))
    assert lines_at()[1] == "Problem: price check failed at 7:36 AM PT, see logs/snapshot_2026-10-22.log"


def test_prices_not_updating_is_a_problem(tmp_path):
    day(tmp_path)
    assert lines_at(CHECK_AT + timedelta(hours=2, minutes=1))[1] == (
        "Problem: no price check since 7:35 AM PT, see logs/snapshot_2026-10-22.log")
    assert lines_at(CHECK_AT + timedelta(hours=2))[1].startswith("Running fine")


def test_no_database_yet():
    assert lines_at() == ["SPORTS-EDGE · 7:37 AM PT", "Problem: no price check has run yet, see logs/"]


def test_status_writes_nothing_and_opens_the_database_read_only(tmp_path):
    day(tmp_path)
    roots = [config.data_dir(), config.logs_dir(), config.state_dir(), config.sheet_dir()]
    snap = lambda: {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for r in roots if r.exists()
                    for p in sorted(r.rglob("*")) if p.is_file()}
    before = snap()
    lines_at()
    assert snap() == before
    with Store.open(read_only=True) as s, pytest.raises(duckdb.Error):
        s.con.execute("CREATE TABLE x (a INTEGER)")
