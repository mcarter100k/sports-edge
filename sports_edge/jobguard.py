"""Run a scheduled job's module under a hard wall-clock limit (docs/specs/heartbeat_spec.md).

python -m sports_edge.jobguard <module> [args...]

At the limit, faulthandler writes every thread's stack to stderr and the
process exits 1, so a hung run ends in the day log as a failure instead of
holding the database lock and blocking launchd. SPORTS_EDGE_JOB_LIMIT_S
exists for the tests only; launchd never sets it.
"""
import faulthandler
import os
import runpy
import sys

LONG_LIMIT_S = 3600   # a slow-Kalshi daily: 2,200 s stopped a daily run on 2026-10-24 (spec amendment 2026-10-25)
LONG_JOBS = ("sports_edge.snapshot", "sports_edge.grader")   # the grader runs the snapshot's settlement sweep
LIMIT_S = 120         # every other job


def limit_s(module):
    override = os.environ.get("SPORTS_EDGE_JOB_LIMIT_S")
    if override:
        return float(override)
    return LONG_LIMIT_S if module in LONG_JOBS else LIMIT_S


def main(argv):
    if not argv:
        print("usage: jobguard <module> [args...]", file=sys.stderr)
        return 2
    faulthandler.dump_traceback_later(limit_s(argv[0]), exit=True)
    sys.argv = list(argv)   # runpy puts the module's file path in argv[0]
    runpy.run_module(argv[0], run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
