#!/usr/bin/env bash
# Run AgentHQ agent in the background with auto-restart and logging.
# Usage: ./run.sh [start|stop|status|log]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${SCRIPT_DIR}/config.yaml"
LOG="${SCRIPT_DIR}/agent.log"
PIDFILE="${SCRIPT_DIR}/.agent.pid"

start() {
    if running; then
        echo "Agent already running (PID $(cat "$PIDFILE"))"
        return 1
    fi
    echo "Starting AgentHQ agent..."
    nohup python3 "${SCRIPT_DIR}/agenthq_agent.py" --config "$CONFIG" >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "Agent started (PID $!) — log: $LOG"
}

stop() {
    if ! running; then
        echo "Agent not running"
        rm -f "$PIDFILE"
        return 1
    fi
    local pid
    pid=$(cat "$PIDFILE")
    echo "Stopping agent (PID $pid)..."
    kill "$pid" 2>/dev/null
    # Wait up to 5s for graceful shutdown
    for i in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
    # Force kill if still alive
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    rm -f "$PIDFILE"
    echo "Agent stopped"
}

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

status() {
    if running; then
        echo "Agent running (PID $(cat "$PIDFILE"))"
    else
        echo "Agent not running"
        rm -f "$PIDFILE"
    fi
}

case "${1:-start}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    log)     tail -f "$LOG" ;;
    *)       echo "Usage: $0 {start|stop|restart|status|log}" ;;
esac
