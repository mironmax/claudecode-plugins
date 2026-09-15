#!/bin/bash
# Knowledge Graph MCP Server Management Script

# Resolve symlinks to get actual script location
SCRIPT_PATH="${BASH_SOURCE[0]}"
if [ -L "$SCRIPT_PATH" ]; then
    SCRIPT_PATH="$(readlink -f "$SCRIPT_PATH")"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
SERVER_SCRIPT="$SCRIPT_DIR/mcp_streamable_server.py"
PID_FILE="$SCRIPT_DIR/.mcp_server.pid"
STORAGE_ROOT="$HOME/.knowledge-graph"
# Log lives in the user's state dir, not world-writable /tmp (predictable /tmp
# paths are symlink-clobber bait on shared machines). Not under STORAGE_ROOT —
# that's a git repo with auto-commit, and logs don't belong in it.
LOG_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/knowledge-graph/mcp_server.log"
PORT="${KG_HTTP_PORT:-8765}"
HOST="${KG_HTTP_HOST:-127.0.0.1}"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
DEPS_MARKER="$SCRIPT_DIR/venv/.deps_ok"
# Why a start failure needs a file: the SessionStart hook backgrounds this
# script and exits immediately, so it never sees the outcome. Without a
# breadcrumb it tells every future session the environment is "warming up"
# for a server that will never come up.
BREADCRUMB="$SCRIPT_DIR/.last_start_error"

# sha256 of a file, portable across Linux and macOS. Empty when no hasher is
# available — callers then fall back to presence-only checking rather than
# reinstalling dependencies on every single start.
file_hash() {
    if command -v sha256sum > /dev/null 2>&1; then
        sha256sum "$1" 2>/dev/null | cut -d' ' -f1
    elif command -v shasum > /dev/null 2>&1; then
        shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1
    else
        echo ""
    fi
}

# The declared mcp range, quoted back in failure messages so the user sees
# what was asked for next to what got installed.
mcp_requirement() {
    grep -E '^[[:space:]]*mcp[<>=!]' "$REQUIREMENTS" 2>/dev/null | head -1
}

# The cheapest check that exercises the real wiring. Import-time API drift —
# mcp 2.x dropping the @list_tools()/@call_tool() decorators — fails exactly
# here, while every plain import still resolves and pip still exits 0.
venv_smoke() {
    (cd "$SCRIPT_DIR" && "$VENV_PYTHON" -c \
        'import mcp_streamable_server as m; m.create_mcp_server()') 2>&1
}

write_breadcrumb() {
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
    {
        echo "when: $(date '+%Y-%m-%d %H:%M:%S')"
        echo "cause: $1"
        echo "log: $LOG_FILE"
    } > "$BREADCRUMB" 2>/dev/null
}

# Reduce a smoke-test or log failure to one sentence naming the cause. The
# server's preflight emits a "KG PREFLIGHT:" line for the known API-drift
# case; anything else falls back to the last exception line.
classify_failure() {
    local text="$1" line
    line=$(printf '%s\n' "$text" | grep -m1 "KG PREFLIGHT:")
    if [ -n "$line" ]; then
        printf '%s' "${line#*KG PREFLIGHT: }"
        return
    fi
    line=$(printf '%s\n' "$text" | grep -E "^[A-Za-z_.]*(Error|Exception):" | tail -1)
    if [ -n "$line" ]; then
        printf '%s' "$line"
        return
    fi
    printf '%s' "server did not answer /health within 10s"
}

# Create the Python venv on first run (or after a plugin update wiped it —
# every update installs into a fresh version-stamped cache dir, so the venv
# must be rebuildable, not a one-time setup step).
ensure_venv() {
    local want
    want=$(file_hash "$REQUIREMENTS")
    # Short-circuit only when the venv exists AND was built from these exact
    # requirements. The marker used to record merely that pip exited 0, and it
    # latched forever — so a changed pin could never reach an existing install
    # and the only way a dependency fix propagated was riding a version bump
    # into a fresh cache dir. Dependency fixes should not need a release.
    if [ -x "$VENV_PYTHON" ] && [ -f "$DEPS_MARKER" ]; then
        if [ -z "$want" ] || [ "$(cat "$DEPS_MARKER" 2>/dev/null)" = "$want" ]; then
            return 0
        fi
        echo "Dependencies changed since this environment was built — reinstalling..."
    fi
    local py
    py=$(command -v python3 || command -v python)
    if [ -z "$py" ]; then
        echo "ERROR: python3 not found. Install Python 3.10+ and run 'kg-memory start' again."
        write_breadcrumb "python3 not found on PATH — install Python 3.10+"
        return 1
    fi
    if [ ! -x "$VENV_PYTHON" ]; then
        echo "First run: setting up Python environment (one-time, ~1 min)..."
        "$py" -m venv "$SCRIPT_DIR/venv" || {
            echo "ERROR: could not create venv"
            write_breadcrumb "could not create the Python venv at $SCRIPT_DIR/venv"
            return 1
        }
    else
        echo "Requirements changed: updating Python environment (~1 min)..."
    fi
    # Eager: the tree converges on what a fresh install resolves, so a venv
    # never keeps a transitive that only the first install chose.
    if ! "$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check --upgrade --upgrade-strategy eager -r "$REQUIREMENTS"; then
        echo "ERROR: dependency install failed — will retry on next start"
        write_breadcrumb "pip install from requirements.txt failed"
        return 1
    fi
    # Installed is not the same as working: verify before latching the marker.
    local smoke_out cause
    if ! smoke_out=$(venv_smoke); then
        cause=$(classify_failure "$smoke_out")
        echo "ERROR: dependencies installed but the server cannot start."
        echo "  $cause"
        echo "  Required: $(mcp_requirement)"
        write_breadcrumb "$cause"
        return 1
    fi
    printf '%s\n' "$want" > "$DEPS_MARKER"
    echo "✓ Python environment ready"
}

# Wait for server health endpoint to respond (up to $1 seconds)
wait_healthy() {
    local timeout="${1:-10}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if curl -sf "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

# Wait for port to be free (up to $1 seconds)
wait_port_free() {
    local timeout="${1:-10}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if ! curl -sf "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

# Auto-commit changes in ~/.knowledge-graph/ (throttled to once per 10 min)
commit_storage() {
    if [ ! -d "$STORAGE_ROOT/.git" ]; then
        return
    fi

    cd "$STORAGE_ROOT" || return

    # Check if there are changes
    if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
        return
    fi

    # Throttle: skip if last commit was < 10 min ago
    LAST_COMMIT=$(git log -1 --format=%ct 2>/dev/null || echo 0)
    NOW=$(date +%s)
    ELAPSED=$((NOW - LAST_COMMIT))
    if [ "$ELAPSED" -lt 600 ] && [ "$1" != "--force" ]; then
        return
    fi

    git add -A
    git commit -m "Auto-save $(date '+%Y-%m-%d %H:%M')" --quiet 2>/dev/null
}

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Server already running (PID: $PID)"
            return 1
        else
            rm "$PID_FILE"
        fi
    fi

    ensure_venv || return 1

    echo "Starting MCP Streamable HTTP Server..."
    mkdir -p "$(dirname "$LOG_FILE")"
    # Launch in a new session so the server is fully detached from the
    # calling process tree (critical when called from within Claude Code).
    # setsid is Linux-only; fall back to nohup on macOS.
    if command -v setsid > /dev/null 2>&1; then
        setsid "$VENV_PYTHON" "$SERVER_SCRIPT" > "$LOG_FILE" 2>&1 &
    else
        nohup "$VENV_PYTHON" "$SERVER_SCRIPT" > "$LOG_FILE" 2>&1 &
    fi
    echo $! > "$PID_FILE"
    # Disown so the shell doesn't track this job
    disown $! 2>/dev/null

    # Wait for server to be healthy (up to 10s).
    #
    # A healthy /health is NOT proof that OUR process came up: if something
    # else already holds the port, the process launched above dies on bind
    # while the incumbent keeps answering, and reporting "started" then is a
    # lie that also skips the breadcrumb. Require both — the port answers AND
    # the process we launched is still alive.
    local launched
    launched=$(cat "$PID_FILE" 2>/dev/null)
    if wait_healthy 10 && ps -p "$launched" > /dev/null 2>&1; then
        rm -f "$BREADCRUMB"
        echo "Server started (PID: $launched)"
        echo "Logs: $LOG_FILE"
    else
        local cause
        if ps -p "$launched" > /dev/null 2>&1; then
            cause=$(classify_failure "$(tail -40 "$LOG_FILE" 2>/dev/null)")
        elif curl -sf "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
            cause="port $PORT is already served by another process — the server we launched exited immediately (run '$0 stop-port' to clear it)"
        else
            cause=$(classify_failure "$(tail -40 "$LOG_FILE" 2>/dev/null)")
        fi
        write_breadcrumb "$cause"
        echo "Failed to start server: $cause"
        echo "Check $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Verify a PID actually belongs to our MCP server process
is_our_server() {
    local pid="$1"
    local cmd
    cmd=$(ps -p "$pid" -o args= 2>/dev/null)
    echo "$cmd" | grep -q "mcp_streamable_server\|mcp_http"
}

# Kill our server by PID with safety check, returns 0 if killed
safe_kill_server() {
    local pid="$1"
    if ! ps -p "$pid" > /dev/null 2>&1; then
        return 1
    fi
    if ! is_our_server "$pid"; then
        local cmd
        cmd=$(ps -p "$pid" -o args= 2>/dev/null)
        echo "WARNING: PID $pid is NOT the MCP server (cmd: $cmd)"
        echo "Refusing to kill. Removing stale PID file."
        return 1
    fi
    kill -TERM "$pid" 2>/dev/null
    local waited=0
    while [ "$waited" -lt 5 ] && ps -p "$pid" > /dev/null 2>&1; do
        sleep 1
        waited=$((waited + 1))
    done
    if ps -p "$pid" > /dev/null 2>&1; then
        echo "Force killing..."
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    return 0
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Server is not running"
        return 1
    fi

    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Stopping server (PID: $PID)..."
        if safe_kill_server "$PID"; then
            rm -f "$PID_FILE"
            echo "Server stopped"
        else
            rm -f "$PID_FILE"
            # Fallback: try to find and stop by port
            echo "Attempting to stop by port..."
            stop_port
            return $?
        fi
    else
        echo "Server not running (stale PID file)"
        rm -f "$PID_FILE"
    fi

    # Commit storage changes on stop
    commit_storage --force
}

stop_port() {
    # Fallback: stop server by finding the process on the configured port
    local pids
    pids=$(lsof -ti:"$PORT" 2>/dev/null)
    if [ -z "$pids" ]; then
        echo "No process found on port $PORT"
        rm -f "$PID_FILE"
        return 1
    fi

    for p in $pids; do
        if is_our_server "$p"; then
            echo "Stopping server on port $PORT (PID: $p)..."
            kill -TERM "$p" 2>/dev/null
        fi
    done

    sleep 2
    rm -f "$PID_FILE"

    # Verify
    local remaining
    remaining=$(lsof -ti:"$PORT" 2>/dev/null)
    if [ -n "$remaining" ]; then
        for p in $remaining; do
            if is_our_server "$p"; then
                echo "Force killing PID $p..."
                kill -9 "$p" 2>/dev/null
            fi
        done
    fi

    echo "Server stopped"
    commit_storage --force
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Server is running (PID: $PID)"
            curl -s "http://${HOST}:${PORT}/health" | python3 -m json.tool 2>/dev/null
            return 0
        else
            echo "Server is not running (stale PID file)"
            return 1
        fi
    else
        echo "Server is not running"
        return 1
    fi
}

restart() {
    # Graceful restart: stop old server, wait for port, start new one.
    # The new server runs in its own session (setsid) so it's fully
    # detached from the caller -- safe to run from within Claude Code.
    echo "Restarting server..."

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Stopping old server (PID: $PID)..."
            if ! safe_kill_server "$PID"; then
                # PID was recycled — try port-based stop
                stop_port
            fi
            rm -f "$PID_FILE"
            commit_storage --force
        else
            rm -f "$PID_FILE"
        fi
    fi

    # Wait for port to be free (the OS may hold it briefly after process exit)
    #
    # Stopping by PID is not enough. A stale PID file — recorded process gone,
    # real server still listening — leaves the incumbent untouched, and then
    # the new process dies on a busy port while /health keeps answering from
    # the old one. Observed 2026-08-05: a restart reported success while the
    # server kept running months-old code. If the port is still answering,
    # find its owner and stop that.
    if ! wait_port_free 5; then
        echo "Port $PORT still in use after stopping by PID — stopping by port..."
        stop_port
        wait_port_free 5
    fi

    start
}

logs() {
    tail -f "$LOG_FILE"
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    commit)
        commit_storage --force
        ;;
    stop-port)
        stop_port
        ;;
    *)
        echo "Usage: $0 {start|stop|stop-port|restart|status|logs|commit}"
        exit 1
        ;;
esac
