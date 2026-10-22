#!/bin/bash
# run_snapshot.sh: the 15-minute snapshot cycle under launchd, then the slate's
# pre-game alert step (docs/specs/snapshot_job_spec.md, docs/specs/heartbeat_spec.md,
# docs/specs/slate_alerts_spec.md). Exits nonzero if either step failed.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"

# Forced-failure switch: proves the alert path through the real launchd job.
if [ -f "$STATE_DIR/force_failure_once" ]; then
    rm -f "$STATE_DIR/force_failure_once"
    export KALSHI_BASE_URL="https://kalshi-forced-failure.invalid/trade-api/v2"
    echo "FORCED FAILURE requested by $STATE_DIR/force_failure_once at $(stamp)" \
        >> "$LOG_DIR/snapshot_${DAY}.log"
fi

run_job snapshot sports_edge.snapshot
RC_SNAP=$?
# Always run, even after a failed snapshot: that is when it sends "Don't bet now".
run_job slate_alert sports_edge.slate alert
RC_ALERT=$?
[ "$RC_SNAP" -ne 0 ] && exit "$RC_SNAP"
exit "$RC_ALERT"
