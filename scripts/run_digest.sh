#!/bin/bash
# run_digest.sh: the 7:30 AM Pacific daily digest (docs/specs/daily_digest_spec.md, Part B).
# ALWAYS notifies: the banner on success (the click opens the report), a loud
# DIGEST FAILED alert otherwise. Exits nonzero on any failure.

. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"

LOG="$LOG_DIR/digest_job_${DAY}.log"
ERR="$(mktemp)"
if [ -n "$ENV_MISSING" ]; then
    OUT=""; RC=1; echo ".env missing at $PROJ/.env" > "$ERR"
else
    OUT="$("$PY" -m sports_edge.digest 2>"$ERR")"; RC=$?
fi
{ echo "===== digest $(stamp) ====="; echo "$OUT"
  if [ -s "$ERR" ]; then echo "----- stderr (exit $RC) -----"; cat "$ERR"; fi; } >> "$LOG"

BANNER="$(printf '%s\n' "$OUT" | sed -n 1p)"
REPORT="$(printf '%s\n' "$OUT" | sed -n 2p | sed -n 's/^report: //p')"
REASON=""
if [ "$RC" -ne 0 ]; then
    REASON="exit $RC: $(grep -v '^[[:space:]]' "$ERR" | grep -v '^Traceback' | tail -1 | cut -c1-160)"
elif grep -q 'Traceback (most recent call last)' "$ERR"; then
    REASON="traceback with exit 0: $(tail -1 "$ERR" | cut -c1-160)"
elif [ "${BANNER#Sports-edge: }" = "$BANNER" ]; then
    REASON="no banner in the output"
elif [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
    REASON="report file missing: ${REPORT:-none named}"
fi
rm -f "$ERR"

if [ -z "$REASON" ]; then
    notify DIGEST "$BANNER" "$REPORT" || exit 1
    exit 0
fi
echo "DIGEST FAILED | $REASON" >> "$LOG"
notify ALERT "DIGEST FAILED | $REASON" "$LOG" loud
exit 1
