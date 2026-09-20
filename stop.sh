#!/usr/bin/env bash
#
# stop.sh — Stop the Smart Member Growth Tracker (backend + dashboard).
#
# Reads the PIDs recorded by start.sh under ./.run and terminates each
# process (and its children), then removes the PID files. As a safety net it
# also frees the configured ports if anything is still listening.
#
# Usage:
#   ./stop.sh              # stop backend + frontend
#   ./stop.sh --backend    # stop backend only
#   ./stop.sh --frontend   # stop frontend only
#
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$PROJECT_DIR/.run"

BACKEND_PORT="${MGT_BACKEND_PORT:-8000}"
FRONTEND_PORT="${MGT_FRONTEND_PORT:-5176}"

STOP_BACKEND=1
STOP_FRONTEND=1
case "${1:-}" in
  --backend)  STOP_FRONTEND=0 ;;
  --frontend) STOP_BACKEND=0 ;;
  "")         ;;
  *) echo "Unknown option: $1"; echo "Usage: ./stop.sh [--backend|--frontend]"; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# Stop a process (and its process group / children) by recorded PID file.
# ---------------------------------------------------------------------------
stop_by_pidfile() {
  local name="$1" pidfile="$2"
  if [ ! -f "$pidfile" ]; then
    echo "•  $name: no PID file (not started via start.sh?)."
    return 0
  fi
  local pid; pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    echo "•  $name: empty PID file — removing."
    rm -f "$pidfile"; return 0
  fi
  if kill -0 "$pid" 2>/dev/null; then
    echo "■  Stopping $name (PID $pid) ..."
    # Kill the whole process group so child procs (e.g. vite/esbuild, uvicorn
    # reloader) are terminated too. Fall back to the bare PID if that fails.
    kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    # Give it a moment to exit gracefully, then force-kill if needed.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "   still alive — sending SIGKILL."
      kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    fi
  else
    echo "•  $name: PID $pid not running — cleaning up."
  fi
  rm -f "$pidfile"
}

# ---------------------------------------------------------------------------
# Safety net: free a port if something is still bound to it.
# ---------------------------------------------------------------------------
free_port() {
  local name="$1" port="$2"
  command -v lsof >/dev/null 2>&1 || return 0
  local pids; pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "■  Freeing $name port $port (PIDs: $pids) ..."
    # shellcheck disable=SC2086
    kill -TERM $pids 2>/dev/null || true
    sleep 1
    pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [ -n "$pids" ] && kill -KILL $pids 2>/dev/null || true
  fi
}

if [ "$STOP_FRONTEND" -eq 1 ]; then
  stop_by_pidfile "dashboard (Vite)" "$RUN_DIR/frontend.pid"
  free_port "frontend" "$FRONTEND_PORT"
fi

if [ "$STOP_BACKEND" -eq 1 ]; then
  stop_by_pidfile "backend (FastAPI)" "$RUN_DIR/backend.pid"
  free_port "backend" "$BACKEND_PORT"
fi

echo "✅ Stopped."
