import subprocess

import pytest

from sports_edge import config, imessage


class Runner:
    def __init__(self, code=0, err=""):
        self.code, self.err, self.calls = code, err, []

    def __call__(self, args, **kw):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, self.code, "", self.err)


def log_text():
    return (config.logs_dir() / "imessage.log").read_text()


def test_log_mode_records_and_never_runs_osascript():
    runner = Runner()
    assert imessage.send("hello\nworld", runner) == "logged"
    assert runner.calls == [] and "logged-only: hello / world" in log_text()


def test_sends_number_and_text_as_arguments_not_script(monkeypatch):
    monkeypatch.setenv("SPORTS_EDGE_NOTIFY", "on")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    runner = Runner()
    text = 'took "42" \\ end tell'
    assert imessage.send(text, runner) == "sent"
    args = runner.calls[0]
    assert args[0] == "osascript" and args[-2:] == ["+15551234567", text]
    assert all(text not in a and "+1555" not in a for a in args[:-2])
    assert " sent: " in log_text()


def test_missing_number_fails_loudly(monkeypatch):
    monkeypatch.setenv("SPORTS_EDGE_NOTIFY", "on")
    with pytest.raises(imessage.SendFailed, match="IMESSAGE_TO"):
        imessage.send("x", Runner())
    assert "FAILED (IMESSAGE_TO not set)" in log_text()


def test_osascript_failure_fails_loudly(monkeypatch):
    monkeypatch.setenv("SPORTS_EDGE_NOTIFY", "on")
    monkeypatch.setenv("IMESSAGE_TO", "+15551234567")
    with pytest.raises(imessage.SendFailed, match="exit 1"):
        imessage.send("x", Runner(1, "Not authorized to send Apple events to Messages."))
    assert "FAILED (exit 1: Not authorized" in log_text()
