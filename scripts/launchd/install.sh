#!/bin/bash
# install.sh: load the sports-edge launchd jobs for this user.
# Times in the plists are the system time zone (America/Los_Angeles).
set -e
SRC="/Users/quis/sports-edge/scripts/launchd"
DST="$HOME/Library/LaunchAgents"
mkdir -p "$DST" /Users/quis/sports-edge/logs
for label in com.quis.sportsedge.snapshot com.quis.sportsedge.grader com.quis.sportsedge.heartbeat; do
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    cp "$SRC/$label.plist" "$DST/$label.plist"
    launchctl bootstrap "gui/$(id -u)" "$DST/$label.plist"
    echo "loaded $label"
done
