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
MIGRATE_SCRIPT="$SCRIPT_DIR/tools/migrate_storage.py"
PID_FILE="$SCRIPT_DIR/.mcp_server.pid"
STORAGE_ROOT="$HOME/.knowledge-graph"
PORT="${KG_HTTP_PORT:-8765}"
HOST="${KG_HTTP_HOST:-127.0.0.1}"

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

# Run migration if centralized storage is empty but legacy exists
auto_migrate() {
    if [ -f "$STORAGE_ROOT/user.json" ]; then
        return  # Already migrated
    fi

    if [ -f "$HOME/.claude/knowledge/user.json" ]; then
        echo "Migrating legacy storage to $STORAGE_ROOT..."
        "$VENV_PYTHON" "$MIGRATE_SCRIPT" --apply
    fi
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

    # Auto-migrate on first start
    auto_migrate

    echo "Starting MCP Streamable HTTP Server..."
    # Launch in a new session so the server is fully detached from the
    # calling process tree (critical when called from within Claude Code).
    # setsid is Linux-only; fall back to nohup on macOS.
    if command -v setsid > /dev/null 2>&1; then
        setsid "$VENV_PYTHON" "$SERVER_SCRIPT" > /tmp/mcp_server.log 2>&1 &
    else
        nohup "$VENV_PYTHON" "$SERVER_SCRIPT" > /tmp/mcp_server.log 2>&1 &
    fi
    echo $! > "$PID_FILE"
    # Disown so the shell doesn't track this job
    disown $! 2>/dev/null

    # Wait for server to be healthy (up to 10s)
    if wait_healthy 10; then
        echo "Server started (PID: $(cat "$PID_FILE"))"
        echo "Logs: /tmp/mcp_server.log"
    else
        echo "Failed to start server. Check /tmp/mcp_server.log"
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
    wait_port_free 5

    start
}

logs() {
    tail -f /tmp/mcp_server.log
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
    migrate)
        "$VENV_PYTHON" "$MIGRATE_SCRIPT" "${@:2}"
        ;;
    stop-port)
        stop_port
        ;;
    *)
        echo "Usage: $0 {start|stop|stop-port|restart|status|logs|commit|migrate [--apply]}"
        exit 1
        ;;
esac
