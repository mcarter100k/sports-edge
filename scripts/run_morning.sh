#!/bin/bash
# run_morning.sh: the 8:00 AM Pacific morning iMessage (docs/specs/slate_alerts_spec.md).
# The iMessage is the delivery. On any failure (including an iMessage that could
# not be sent) a loud MORNING FAILED notification goes out instead, and it exits 1.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"

LOG="$LOG_DIR/morning_${DAY}.log"
ERR="$(mktemp)"
if [ -n "$ENV_MISSING" ]; then
    OUT=""; RC=1; echo ".env missing at $PROJ/.env" > "$ERR"
else
    OUT="$("$PY" -m sports_edge.slate morning 2>"$ERR")"; RC=$?
fi
{ echo "===== morning $(stamp) ====="; echo "$OUT"
  if [ -s "$ERR" ]; then echo "----- stderr (exit $RC) -----"; cat "$ERR"; fi; } >> "$LOG"

REASON=""
if [ "$RC" -ne 0 ]; then
    REASON="exit $RC: $(grep -v '^[[:space:]]' "$ERR" | grep -v '^Traceback' | tail -1 | cut -c1-160)"
elif grep -q 'Traceback (most recent call last)' "$ERR"; then
    REASON="traceback with exit 0: $(tail -1 "$ERR" | cut -c1-160)"
elif ! printf '%s\n' "$OUT" | grep -q '^SLATE MORNING OK'; then
    REASON="no SLATE MORNING OK line in the output"
fi
rm -f "$ERR"

if [ -z "$REASON" ]; then
    exit 0
fi
echo "MORNING FAILED | $REASON" >> "$LOG"
notify ALERT "MORNING FAILED | $REASON" "$LOG" loud
exit 1
