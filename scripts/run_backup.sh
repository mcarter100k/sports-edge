#!/bin/bash
# run_backup.sh: nightly encrypted off-machine backup under launchd (docs/specs/backup_spec.md).
# Exits nonzero on any failure.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"
run_job backup sports_edge.backup run
exit $?
