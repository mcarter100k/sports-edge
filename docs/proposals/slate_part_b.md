# Proposal: Part B of the slate and alerts (protected; owner review before build)

This document is for review only. Nothing under `scripts/` has been changed.
Each item below is the exact text proposed, so a review can approve or reject
it line by line. The behaviour is specified in `docs/specs/slate_alerts_spec.md`.

## 1. `scripts/common.sh`: `run_job` passes arguments through

```bash
# run_job <job> <python module> [args...]
run_job() {
    _job="$1"; _module="$2"; shift 2
    ...
        "$PY" -m "$_module" "$@" > "$_out" 2> "$_err"
```

`"$@"` after `shift` works in bash 3.2, which is the version launchd runs.
Nothing else in `run_job` changes.

## 2. `scripts/run_snapshot.sh`: stay awake for the run, then always run the alert step

```bash
. "${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}/scripts/common.sh"
caffeinate -i -w $$ &                 # no idle sleep while this script runs

# (forced-failure switch unchanged)

run_job snapshot sports_edge.snapshot
RC_SNAP=$?
run_job slate_alert sports_edge.slate alert   # runs even after a failed snapshot: it sends "Don't bet now"
RC_ALERT=$?
. "$PROJ/scripts/wake.sh"
[ "$RC_SNAP" -ne 0 ] && exit "$RC_SNAP"
exit "$RC_ALERT"
```

- **Failures:** `slate_alert` gets its own health state, so a failed send
  raises the usual loud ALERT once an hour, and a recovery sends RECOVERED.
- **Exit code:** nonzero if either step failed. The snapshot's own failure
  takes precedence.

## 3. `scripts/run_morning.sh` with `com.quis.sportsedge.morning.plist` at 8:00 (replaces the digest job)

```bash
#!/bin/bash
# run_morning.sh: the 8:00 AM Pacific morning iMessage (docs/specs/slate_alerts_spec.md).
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
if [ "$RC" -ne 0 ] || grep -q 'Traceback (most recent call last)' "$ERR" \
        || ! printf '%s\n' "$OUT" | grep -q '^SLATE MORNING OK'; then
    REASON="exit $RC: $(grep -v '^[[:space:]]' "$ERR" | grep -v '^Traceback' | tail -1 | cut -c1-160)"
    rm -f "$ERR"
    notify ALERT "MORNING FAILED | $REASON" "$LOG" loud
    exit 1
fi
rm -f "$ERR"
. "$PROJ/scripts/wake.sh"
exit 0
```

**The plist:** a copy of the digest plist, with these changes:
- the label is `com.quis.sportsedge.morning`;
- it runs `run_morning.sh`;
- it fires at `Hour 8`, `Minute 0`;
- its logs go to `launchd_morning.*`.

**`install.sh` and `uninstall.sh`:** the label list replaces
`com.quis.sportsedge.digest` with `com.quis.sportsedge.morning`. Then delete
`run_digest.sh` and its plist, and remove the digest cases in
`tests/test_wrappers.py` and `tests/test_launchd_config.py`. Those cases
are replaced by the same cases for the morning job.

**Why delivery differs:** a morning that cannot send its iMessage exits
nonzero and raises a Notification Center ALERT. That ALERT is the other
delivery path (notify.sh), so a broken iMessage path is still loud.

## 4. `scripts/wake.sh`: schedule a wake before the first check ahead (sourced)

```bash
# wake.sh: sourced after the alert and morning steps. Needs a sudoers entry (below).
_w="$("$PY" -m sports_edge.slate wake 2>>"$LOG_DIR/wake_${DAY}.log")"
_t="$(printf '%s' "$_w" | sed -n 's/^wake=\([0-9/]* [0-9:]*\) .*/\1/p')"
if [ -n "$_t" ] && [ "$_t" != "$(cat "$STATE_DIR/wake.txt" 2>/dev/null)" ]; then
    if sudo -n /usr/bin/pmset schedule wake "$_t" >>"$LOG_DIR/wake_${DAY}.log" 2>&1; then
        echo "$_t" > "$STATE_DIR/wake.txt"
    else
        notify ALERT "wake not scheduled for $_t (sudo -n pmset failed)" "$LOG_DIR/wake_${DAY}.log" loud
    fi
fi
```

- **Wake time:** 15 minutes before the first check still ahead in the next
  24 hours.
- **Staying awake:** the snapshot run is held awake by `caffeinate -w $$`.
- **Between checks:** on battery the machine sleeps after 1 idle minute
  (observed 2026-10-22). Once launchd notices the wake it fires the missed
  snapshot job, and that run checks whatever is due. Checks later in the day
  each need their own wake, which this file schedules after every run. On AC
  power the machine never sleeps (observed), and none of this matters.

## 5. Owner actions (machine permissions; nothing here is done by the build)

| Action | Why | How |
|---|---|---|
| sudoers entry | `pmset schedule wake` needs root, and `sudo -n` asks for a password today | `sudo visudo -f /etc/sudoers.d/sportsedge-wake` → `quis ALL=(root) NOPASSWD: /usr/bin/pmset schedule wake *` |
| Full Disk Access for the venv's Python | reading `~/Library/Messages/chat.db` returned "authorization denied" | System Settings → Privacy & Security → Full Disk Access → add the interpreter. `venv/bin/python` resolves to `/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14` (observed 2026-10-22). It is NOT CHECKED whether macOS attributes the access to that file or to the framework's `Resources/Python.app`. After granting it, confirm with `venv/bin/python -m sports_edge.slate alert` showing `bet_log=read`, and note that a Homebrew Python upgrade changes the path |
| Automation: Messages | the first `osascript` send prompts for permission | run `venv/bin/python -c "from sports_edge import imessage; imessage.send('test')"` once while logged in, with `IMESSAGE_TO` set and `SPORTS_EDGE_NOTIFY` unset, and allow it |
| `.env` | where messages go, and the plan | `IMESSAGE_TO=+1…`, and optionally `ODDS_PLAN=paid` |

The Full Disk Access grant reaches every script that venv runs. A narrower
alternative is a small signed helper binary that only reads `chat.db`. That
is more work, and is the owner's call.

## PENDING once installed (recorded in `docs/validation_log.md` with dates)

- The first 8:00 AM morning iMessage.
- The first pre-game check iMessage, with picks or "no price gaps".
- The first "Don't bet now" from a real failing run (use `state/force_failure_once`).
- The first reply logged from the phone.
- The first wake fired by `pmset`.
