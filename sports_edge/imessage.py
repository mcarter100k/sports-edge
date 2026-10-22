"""Send one iMessage to the owner's own number (docs/specs/slate_alerts_spec.md, Delivery).

The number and the text are passed to osascript as arguments, never pasted
into the script. SPORTS_EDGE_NOTIFY=log records without sending (tests and
reality runs). Every attempt is appended to logs/imessage.log.
"""
import os
import subprocess

from . import config
from .timeutil import iso_z, utcnow

SCRIPT = (
    "on run argv",
    'tell application "Messages"',
    "set svc to 1st account whose service type = iMessage",
    "send (item 2 of argv) to participant (item 1 of argv) of svc",
    "end tell",
    "end run",
)
TIMEOUT_S = 30


class SendFailed(Exception):
    pass


def _log(status, text):
    path = config.logs_dir() / "imessage.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{iso_z(utcnow())} {status}: {text.replace(chr(10), ' / ')}\n")


def command(to, text):
    args = ["osascript"]
    for line in SCRIPT:
        args += ["-e", line]
    return args + [to, text]


def send(text, runner=subprocess.run):
    """Deliver one message. Returns 'sent' or 'logged'; raises SendFailed otherwise."""
    if os.environ.get("SPORTS_EDGE_NOTIFY", "on") == "log":
        _log("logged-only", text)
        return "logged"
    to = config.imessage_to()
    if not to:
        _log("FAILED (IMESSAGE_TO not set)", text)
        raise SendFailed("IMESSAGE_TO is not set in .env")
    try:
        proc = runner(command(to, text), capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"FAILED ({type(exc).__name__})", text)
        raise SendFailed(f"osascript did not run: {exc}") from exc
    if proc.returncode != 0:
        reason = (proc.stderr or "").strip()[:200]
        _log(f"FAILED (exit {proc.returncode}: {reason})", text)
        raise SendFailed(f"osascript exit {proc.returncode}: {reason}")
    _log("sent", text)
    return "sent"
