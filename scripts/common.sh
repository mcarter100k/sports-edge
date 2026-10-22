# common.sh: shared by the scheduled wrappers (docs/specs/heartbeat_spec.md).
# Sourced, never run. Plain bash 3.2: launchd runs /bin/bash.
#
# SPORTS_EDGE_PROJ, SPORTS_EDGE_PYTHON and SPORTS_EDGE_NO_ENV_FILE exist for
# the tests only; launchd sets none of them.

PROJ="${SPORTS_EDGE_PROJ:-/Users/quis/sports-edge}"
cd "$PROJ" || { echo "cannot cd to $PROJ" >&2; exit 1; }

ENV_MISSING=""
if [ -z "${SPORTS_EDGE_NO_ENV_FILE:-}" ]; then
    if [ -f "$PROJ/.env" ]; then
        set -a; . "$PROJ/.env"; set +a
    else
        ENV_MISSING=1
    fi
fi

LOG_DIR="${SPORTS_EDGE_LOG_DIR:-$PROJ/logs}"
STATE_DIR="${SPORTS_EDGE_STATE_DIR:-$PROJ/state}"
mkdir -p "$LOG_DIR" "$STATE_DIR"
PY="${SPORTS_EDGE_PYTHON:-$PROJ/venv/bin/python}"
DAY="$(TZ=America/Los_Angeles date +%F)"

stamp() { TZ=America/Los_Angeles date '+%Y-%m-%d %H:%M:%S %Z'; }

. "$PROJ/scripts/notify.sh"

# run_job <job> <python module> [args...]
# Runs the job, appends its output to the day log, records health, delivers
# any alert, and returns nonzero on failure. A failed run never returns 0.
run_job() {
    _job="$1"; _module="$2"; shift 2
    _log="$LOG_DIR/${_job}_${DAY}.log"
    _out="$(mktemp)"; _err="$(mktemp)"
    echo "===== $_job $(stamp) =====" >> "$_log"
    if [ -n "$ENV_MISSING" ]; then
        _rc=1
        echo ".env missing at $PROJ/.env" > "$_err"
    else
        "$PY" -m "$_module" "$@" > "$_out" 2> "$_err"
        _rc=$?
    fi
    cat "$_out" >> "$_log"
    if [ -s "$_err" ]; then
        echo "----- stderr (exit $_rc) -----" >> "$_log"
        cat "$_err" >> "$_log"
    fi

    _reason=""
    if [ "$_rc" -ne 0 ] || grep -q 'Traceback (most recent call last)' "$_err"; then
        _last="$(grep -v '^[[:space:]]' "$_err" | grep -v '^Traceback' | grep -v '^UNMATCHED' \
                 | grep -v '^MATCH CONFLICT' | grep -v '^DEVIG REJECTED' | tail -1 | cut -c1-200)"
        _reason="exit $_rc: ${_last:-no error output}"
    elif [ ! -s "$_out" ]; then
        _reason="no output"
    fi

    if [ -n "$_reason" ]; then
        [ "$_rc" -eq 0 ] && _rc=1
        _msg="$("$PY" -m sports_edge.health record-failure "$_job" "$_reason" 2>>"$_log")"
        _hrc=$?
        if [ "$_hrc" -ne 0 ]; then
            _msg="ALERT:$_job failed: $_reason (health state unavailable, exit $_hrc)"
        fi
    else
        _msg="$("$PY" -m sports_edge.health record-success "$_job" 2>>"$_log")"
        _hrc=$?
        if [ "$_hrc" -ne 0 ]; then
            _msg="ALERT:$_job ran but its health state could not be recorded (exit $_hrc)"
            _rc=1
        fi
    fi

    if [ -n "$_msg" ]; then
        echo "$_msg" >> "$_log"
        case "$_msg" in
            ALERT:*)     notify ALERT "${_msg#ALERT:}" "$_log" loud || _rc=1 ;;
            RECOVERED:*) notify RECOVERED "${_msg#RECOVERED:}" "$_log" ;;
        esac
    fi
    rm -f "$_out" "$_err"
    return "$_rc"
}
