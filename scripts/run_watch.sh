#!/bin/bash
# run_watch.sh: alerts when no snapshot run has finished for an hour, from outside
# the snapshot job, so a run that never ends is still reported
# (docs/specs/heartbeat_spec.md, amendment 2026-10-24). Exits nonzero when stale.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"
run_job snapshot_watch sports_edge.health check-stale snapshot
exit $?
