"""Run a scheduled job's module under a hard wall-clock limit (docs/specs/heartbeat_spec.md).

python -m sports_edge.jobguard <module> [args...]

At the limit, faulthandler writes every thread's stack to stderr and the
process exits 1, so a hung run ends in the day log as a failure instead of
holding the database lock and blocking launchd. SPORTS_EDGE_JOB_LIMIT_S
exists for the tests only (it replaces every limit); launchd never sets it.
"""
import faulthandler
import os
import runpy
import sys

# Seconds: the module's own lock wait + max(2x its slowest normal run, 120) (amendment 2026-10-24).
LIMITS_S = {
    "sports_edge.snapshot": 720,    # 600 s lock wait + 120 s, until the tier is known (SNAPSHOT_TIER_S)
    "sports_edge.grader": 720,      # 600 s lock wait + 120 s (slowest normal run 10 s)
    "sports_edge.slate": 240,       # 120 s lock wait + 120 s (alert about 6 s, morning about 8 s)
    "sports_edge.heartbeat": 240,   # 120 s lock wait + 120 s (never run under launchd)
    "sports_edge.health": 120,      # the watch: no lock, reads health.json only
    "sports_edge.backup": 2200,     # never run: not measured; set from its first real runs
}
# From the moment the snapshot knows its tier.
SNAPSHOT_TIER_S = {
    "near": 200,      # slowest normal 99 s
    "hourly": 120,    # slowest normal 31 s
    "daily": 2200,    # 2x the 1,103 s catch-up run of 2026-10-24: a killed daily retries as daily
}

_armed = False


def _override():
    value = os.environ.get("SPORTS_EDGE_JOB_LIMIT_S")
    return float(value) if value else None


def set_limit(seconds):
    """Restart the limit at `seconds` from now. Does nothing outside jobguard (tests, manual runs)."""
    if _armed:
        faulthandler.dump_traceback_later(_override() or seconds, exit=True)


def main(argv):
    global _armed
    if not argv:
        print("usage: jobguard <module> [args...]", file=sys.stderr)
        return 2
    limit = _override() or LIMITS_S.get(argv[0])
    if limit is None:
        print(f"jobguard: no time limit set for {argv[0]}", file=sys.stderr)
        return 2
    faulthandler.dump_traceback_later(limit, exit=True)
    _armed = True
    sys.argv = list(argv)   # runpy puts the module's file path in argv[0]
    runpy.run_module(argv[0], run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    # Run as -m this file is __main__; the job imports sports_edge.jobguard, so keep _armed there.
    from sports_edge import jobguard
    sys.exit(jobguard.main(sys.argv[1:]))
