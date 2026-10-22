"""Without IMESSAGE_TO, slate texts are Mac notifications through scripts/notify.sh
(docs/specs/slate_alerts_spec.md, "delivery without iMessage")."""
import os
import stat
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from sports_edge import config, imessage, slate
from test_slate import CHECK_AT, DAILY_AT, quiet, run_at, world

REPO = Path(__file__).resolve().parent.parent
LONG = "Kalshi slate · Fri Oct 23\n" + "\n".join(f"Today: game {i} · 7:00 PM PT · NBA ·· ¢" for i in range(30))


def exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    """Fake terminal-notifier and osascript that record their arguments: no real banner in a test."""
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    exe(bin_ / "terminal-notifier", f'#!/bin/bash\nprintf "%s\\n" "$@" >> {tmp_path}/tn_calls\nexit "${{TN_EXIT:-0}}"\n')
    exe(bin_ / "osascript", f'#!/bin/bash\nprintf "%s\\n" "$@" >> {tmp_path}/osa_calls\nexit "${{OSA_EXIT:-0}}"\n')
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("SPORTS_EDGE_TN_BIN", str(bin_ / "terminal-notifier"))
    monkeypatch.setenv("SPORTS_EDGE_NOTIFY", "on")
    monkeypatch.setenv("SPORTS_EDGE_SHEET_DIR", str(tmp_path / "Mobile Documents" / "sports-edge"))
    calls = lambda name: (tmp_path / name).read_text().splitlines() if (tmp_path / name).exists() else []
    return lambda: calls("tn_calls"), lambda: calls("osa_calls")


def arg(args, flag):
    return args[args.index(flag) + 1]


def test_without_a_number_a_text_is_a_notification_that_opens_the_sheet(fakes):
    tn, osa = fakes
    assert imessage.send('#1 Cleveland over Philadelphia\nReply "took 20" or "skip"\nPractice · 0 of 100 games') == "notified"
    args = tn()
    assert arg(args, "-subtitle") == "SLATE" and "-sound" not in args and osa() == []
    assert arg(args, "-message") == ('#1 Cleveland over Philadelphia · Reply "took 20" or "skip" · '
                                     'Practice · 0 of 100 games → sheet.txt')
    assert arg(args, "-open") == "file://" + str(config.sheet_dir() / "sheet.txt").replace(" ", "%20")
    assert "notified (IMESSAGE_TO not set): #1 Cleveland" in (config.logs_dir() / "imessage.log").read_text()
    assert " SLATE terminal-notifier: #1 Cleveland" in (config.logs_dir() / "alerts.log").read_text()


def test_a_long_text_is_cut_to_300_bytes_on_a_character_boundary(fakes):
    tn, _ = fakes
    imessage.send(LONG)
    message = arg(tn(), "-message")
    assert len(message.encode()) <= 300 and message.endswith("… → sheet.txt") and "�" not in message
    assert message.startswith("Kalshi slate · Fri Oct 23 · Today: game 0")


def test_a_notification_that_cannot_be_shown_fails_loudly(fakes, monkeypatch):
    monkeypatch.setenv("TN_EXIT", "1")
    monkeypatch.setenv("OSA_EXIT", "1")
    with pytest.raises(imessage.SendFailed, match="notification not delivered"):
        imessage.send("Don't bet now: prices not checked (system problem).")
    assert "FAILED (notification exit 1)" in (config.logs_dir() / "imessage.log").read_text()


def test_with_a_number_it_is_an_imessage_and_no_notification(fakes, monkeypatch):
    tn, osa = fakes
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    assert imessage.send("#1 Cleveland") == "sent"
    assert tn() == [] and osa()[-2:] == ["+15551234567", "#1 Cleveland"]


def test_a_slate_alert_without_a_number_is_shown_and_exits_ok(fakes):
    tn, _ = fakes
    sers, pages = world()
    run_at(DAILY_AT, sers, pages)
    run_at(CHECK_AT, sers, pages)
    out = []
    assert slate.run_alert(CHECK_AT + timedelta(minutes=1), out=out.append) == 1
    assert arg(tn(), "-message").startswith("Don't bet today: prices not checked (no fee table).")
    assert out[0].startswith("SLATE ALERT OK messages=1")


def test_the_morning_job_without_a_number_exits_zero_with_a_banner(fakes, tmp_path):
    tn, _ = fakes
    env = {**os.environ, "SPORTS_EDGE_PROJ": str(REPO), "SPORTS_EDGE_PYTHON": sys.executable,
           "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "SPORTS_EDGE_NO_ENV_FILE": "1",
           "HOME": str(tmp_path), "NOTIFY_WATCHDOG_SECS": "3"}
    env.pop("IMESSAGE_TO", None)
    r = subprocess.run(["/bin/bash", str(REPO / "scripts" / "run_morning.sh")], env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert arg(tn(), "-message").startswith("Kalshi slate · ") and arg(tn(), "-subtitle") == "SLATE"


def test_a_failure_alert_also_opens_a_path_with_spaces(fakes, tmp_path):
    tn, _ = fakes
    log = tmp_path / "Mobile Documents" / "day.log"
    log.parent.mkdir(exist_ok=True)
    subprocess.run(["/bin/bash", "-c", '. "$1" && notify ALERT boom "$2" loud', "x", str(REPO / "scripts" / "notify.sh"),
                    str(log)], env={**os.environ, "LOG_DIR": str(config.logs_dir())}, check=True, timeout=30)
    assert "-sound" in tn() and arg(tn(), "-open") == "file://" + str(log).replace(" ", "%20")
