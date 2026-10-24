#!/bin/bash
# uninstall.sh: unload the sports-edge launchd jobs. Data and logs are kept.
for label in com.quis.sportsedge.snapshot com.quis.sportsedge.grader com.quis.sportsedge.heartbeat com.quis.sportsedge.morning com.quis.sportsedge.backup com.quis.sportsedge.watch; do
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "unloaded $label"
    rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
