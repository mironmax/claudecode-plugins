#!/bin/bash
# Knowledge Graph Visual Editor Management Script

# Resolve symlinks to get actual script location
SCRIPT_PATH="${BASH_SOURCE[0]}"
if [ -L "$SCRIPT_PATH" ]; then
    SCRIPT_PATH="$(readlink -f "$SCRIPT_PATH")"
fi
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
SERVER_SCRIPT="$SCRIPT_DIR/backend/server.py"
PID_FILE="$SCRIPT_DIR/.visual_editor.pid"
# Log lives in the user's state dir, not world-writable /tmp (and not in
# ~/.knowledge-graph, which is a git repo with auto-commit).
LOG_FILE="${XDG_STATE_HOME:-$HOME/.local/state}/knowledge-graph/visual_editor.log"
PORT="${EDITOR_PORT:-3000}"

# Create the Python venv on first run (or after a plugin update wiped it —
# every update installs into a fresh version-stamped cache dir).
ensure_venv() {
    if [ -x "$VENV_PYTHON" ] && [ -f "$SCRIPT_DIR/venv/.deps_ok" ]; then
        return 0
    fi
    local py
    py=$(command -v python3 || command -v python)
    if [ -z "$py" ]; then
        echo "ERROR: python3 not found. Install Python 3.10+ and run 'kg-visual start' again."
        return 1
    fi
    echo "First run: setting up Python environment (one-time, ~1 min)..."
    if [ ! -x "$VENV_PYTHON" ]; then
        "$py" -m venv "$SCRIPT_DIR/venv" || { echo "ERROR: could not create venv"; return 1; }
    fi
    if "$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"; then
        touch "$SCRIPT_DIR/venv/.deps_ok"
        echo "✓ Python environment ready"
    else
        echo "ERROR: dependency install failed — will retry on next start"
        return 1
    fi
}

start() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Visual editor already running (PID: $PID)"
            echo "Open: http://localhost:$PORT"
            return 1
        else
            rm "$PID_FILE"
        fi
    fi

    ensure_venv || return 1

    echo "Starting Visual Editor..."
    mkdir -p "$(dirname "$LOG_FILE")"
    nohup "$VENV_PYTHON" "$SERVER_SCRIPT" > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2

    if ps -p $(cat "$PID_FILE") > /dev/null 2>&1; then
        echo "Visual editor started (PID: $(cat "$PID_FILE"))"
        echo "Open: http://localhost:$PORT"
        echo "Logs: $LOG_FILE"
    else
        echo "Failed to start. Check $LOG_FILE"
        rm "$PID_FILE"
        return 1
    fi
}

stop() {
    if [ ! -f "$PID_FILE" ]; then
        echo "Visual editor is not running"
        return 1
    fi

    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "Stopping visual editor (PID: $PID)..."
        kill "$PID"
        sleep 2

        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Force killing..."
            kill -9 "$PID"
        fi

        rm "$PID_FILE"
        echo "Visual editor stopped"
    else
        echo "Visual editor not running (stale PID file)"
        rm "$PID_FILE"
    fi
}

status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "Visual editor is running (PID: $PID)"
            echo "URL: http://localhost:$PORT"
            curl -s "http://127.0.0.1:$PORT/api/health" | python3 -m json.tool 2>/dev/null
            return 0
        else
            echo "Visual editor is not running (stale PID file)"
            return 1
        fi
    else
        echo "Visual editor is not running"
        return 1
    fi
}

restart() {
    stop
    sleep 1
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
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
