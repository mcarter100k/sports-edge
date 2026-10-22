#!/bin/bash
# run_grader.sh: the nightly grader under launchd (docs/specs/grader_spec.md).
# Exits nonzero on any failure.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"
run_job grader sports_edge.grader
exit $?
