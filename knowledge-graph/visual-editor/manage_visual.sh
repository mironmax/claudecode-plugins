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
LOG_FILE="/tmp/visual_editor.log"
PORT="${EDITOR_PORT:-3000}"

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

    echo "Starting Visual Editor..."
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
