"""When an alert reaches the owner (docs/specs/heartbeat_spec.md).

python -m sports_edge.health record-failure <job> <reason>
python -m sports_edge.health record-success <job>

Prints "ALERT:<message>" or "RECOVERED:<message>" when the owner should be
told, and nothing otherwise. The wrapper delivers what is printed.
"""
import fcntl
import json
import os
import sys
from datetime import timedelta

from . import config
from .timeutil import display, iso_z, parse_utc, utcnow

REMIND_EVERY = timedelta(minutes=60)


def _path():
    return config.state_dir() / "health.json"


def _load(fh):
    fh.seek(0)
    text = fh.read()
    return json.loads(text) if text.strip() else {}


def _save(state):
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _update(fn):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with open(path, "a+") as fh:
            state = _load(fh)
        message = fn(state)
        _save(state)
        return message


def record_failure(job, reason, now=None):
    now = now or utcnow()

    def apply(state):
        s = state.setdefault(job, {})
        s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
        s["last_failure_utc"] = iso_z(now)
        s["last_reason"] = reason
        last_alert = parse_utc(s.get("last_alert_utc"))
        n = s["consecutive_failures"]
        if n == 1:
            s["last_alert_utc"] = iso_z(now)
            return f"ALERT:{job} failed: {reason}"
        if last_alert is None or now - last_alert >= REMIND_EVERY:
            s["last_alert_utc"] = iso_z(now)
            return f"ALERT:{job} still failing ({n} in a row): {reason}"
        return None

    return _update(apply)


def record_success(job, now=None):
    now = now or utcnow()

    def apply(state):
        s = state.setdefault(job, {})
        n = s.get("consecutive_failures", 0)
        s["consecutive_failures"] = 0
        s["last_success_utc"] = iso_z(now)
        if n:
            s.pop("last_alert_utc", None)
            return f"RECOVERED:{job} ok again at {display(now)} after {n} failure(s)"
        return None

    return _update(apply)


def read_state():
    path = _path()
    if not path.exists():
        return {}
    with open(path) as fh:
        return _load(fh)


def main(argv):
    if len(argv) >= 3 and argv[0] == "record-failure":
        message = record_failure(argv[1], " ".join(argv[2:]))
    elif len(argv) == 2 and argv[0] == "record-success":
        message = record_success(argv[1])
    else:
        print("usage: health record-failure <job> <reason> | record-success <job>", file=sys.stderr)
        return 2
    if message:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
