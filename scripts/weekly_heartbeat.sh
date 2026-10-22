#!/bin/bash
# weekly_heartbeat.sh: Sunday proof of life (docs/specs/heartbeat_spec.md).
# ALWAYS notifies, healthy or not: a missing Sunday notification is itself
# the alarm. Exits 1 when attention is needed or the report failed.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"

LOG="$LOG_DIR/heartbeat_${DAY}.log"
ERR="$(mktemp)"
if [ -n "$ENV_MISSING" ]; then
    OUT=""; RC=1; echo ".env missing at $PROJ/.env" > "$ERR"
else
    OUT="$("$PY" -m sports_edge.heartbeat 2>"$ERR")"; RC=$?
fi
{ echo "===== heartbeat $(stamp) ====="; echo "$OUT"
  if [ -s "$ERR" ]; then echo "----- stderr (exit $RC) -----"; cat "$ERR"; fi; } >> "$LOG"

if [ "$RC" -eq 0 ] || [ "$RC" -eq 1 ]; then
    if grep -q 'Traceback (most recent call last)' "$ERR" || [ -z "$OUT" ]; then
        HEAD="NEEDS ATTENTION | heartbeat report failed: $(tail -1 "$ERR" | cut -c1-160)"; RC=1
    else
        HEAD="$(printf '%s\n' "$OUT" | head -1)"
    fi
else
    HEAD="NEEDS ATTENTION | heartbeat report crashed (exit $RC): $(tail -1 "$ERR" | cut -c1-160)"; RC=1
fi
rm -f "$ERR"

if [ "$RC" -eq 0 ]; then
    notify HEARTBEAT "$HEAD" "$LOG" || RC=1
else
    notify HEARTBEAT "$HEAD" "$LOG" loud
    RC=1
fi
exit "$RC"
