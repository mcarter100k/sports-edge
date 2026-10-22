# notify.sh: deliver one notification to the owner (docs/specs/heartbeat_spec.md).
# Sourced by common.sh. Requires LOG_DIR.
#
#   notify <CATEGORY> <MESSAGE> <DAY-LOG> [loud]
#
# terminal-notifier under a 15 s watchdog (its first post can block on a
# permission dialog), then osascript. Every attempt is written to
# logs/alerts.log. Returns 1 only if both methods failed.
# SPORTS_EDGE_NOTIFY=log records without posting (tests and reality runs).

TN_BIN="${SPORTS_EDGE_TN_BIN:-$HOME/Applications/terminal-notifier.app/Contents/MacOS/terminal-notifier}"
NOTIFY_WATCHDOG_SECS="${NOTIFY_WATCHDOG_SECS:-15}"

_utc() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

notify() {
    _n_cat="$1"; _n_msg="$2"; _n_log="$3"; _n_loud="${4:-}"
    _n_alog="$LOG_DIR/alerts.log"
    _n_text="$(printf '%s → %s' "$_n_msg" "$(basename "$_n_log")" | cut -c1-300)"
    if [ "${SPORTS_EDGE_NOTIFY:-on}" = "log" ]; then
        echo "$(_utc) $_n_cat logged-only: $_n_text" >> "$_n_alog"
        return 0
    fi
    _n_sound=""
    [ -n "$_n_loud" ] && _n_sound="Basso"

    if [ -x "$TN_BIN" ]; then
        if [ -n "$_n_sound" ]; then
            "$TN_BIN" -title "sports-edge" -subtitle "$_n_cat" -message "$_n_text" \
                -sound "$_n_sound" -open "file://$_n_log" >/dev/null 2>&1 &
        else
            "$TN_BIN" -title "sports-edge" -subtitle "$_n_cat" -message "$_n_text" \
                -open "file://$_n_log" >/dev/null 2>&1 &
        fi
        _n_pid=$!
        _n_i=0
        while kill -0 "$_n_pid" 2>/dev/null && [ "$_n_i" -lt "$NOTIFY_WATCHDOG_SECS" ]; do
            sleep 1; _n_i=$((_n_i + 1))
        done
        if kill -0 "$_n_pid" 2>/dev/null; then
            kill "$_n_pid" 2>/dev/null
            _n_ok=1
        else
            wait "$_n_pid"; _n_ok=$?
        fi
        if [ "$_n_ok" -eq 0 ]; then
            echo "$(_utc) $_n_cat terminal-notifier: $_n_text" >> "$_n_alog"
            return 0
        fi
    fi

    _n_safe="$(printf '%s' "$_n_text" | tr '"\\' "''" | cut -c1-220)"
    _n_snd=""
    [ -n "$_n_sound" ] && _n_snd=" sound name \"$_n_sound\""
    if osascript -e "display notification \"$_n_safe\" with title \"sports-edge\" subtitle \"$_n_cat\"$_n_snd" >/dev/null 2>&1; then
        echo "$(_utc) $_n_cat osascript: $_n_text" >> "$_n_alog"
        return 0
    fi
    echo "$(_utc) ALERT DELIVERY FAILED $_n_cat: $_n_text" >> "$_n_alog"
    echo "ALERT DELIVERY FAILED $_n_cat: $_n_text" >> "$_n_log"
    return 1
}
