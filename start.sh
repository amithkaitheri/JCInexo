#!/usr/bin/env bash
#
# start.sh — Start the Smart Member Growth Tracker (backend + dashboard).
#
# Starts two processes:
#   1. FastAPI backend  (uvicorn) on port 8000
#   2. Vite dashboard   (npm run dev) on port 5176
#
# The dashboard's Vite dev server proxies /api/* to the backend on :8000, so
# both run together. PIDs and logs are written under ./.run so stop.sh can
# cleanly shut everything down.
#
# Usage:
#   ./start.sh              # start backend + frontend
#   ./start.sh --backend    # start backend only
#   ./start.sh --frontend   # start frontend only
#
set -euo pipefail

# Resolve this script's directory so it works from anywhere.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
RUN_DIR="$PROJECT_DIR/.run"

BACKEND_PORT="${MGT_BACKEND_PORT:-8000}"
FRONTEND_PORT="${MGT_FRONTEND_PORT:-5176}"

mkdir -p "$RUN_DIR"

START_BACKEND=1
START_FRONTEND=1
case "${1:-}" in
  --backend)  START_FRONTEND=0 ;;
  --frontend) START_BACKEND=0 ;;
  "")         ;;
  *) echo "Unknown option: $1"; echo "Usage: ./start.sh [--backend|--frontend]"; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# Helper: is a recorded PID still alive?
# ---------------------------------------------------------------------------
is_running() {
  local pidfile="$1"
  [ -f "$pidfile" ] || return 1
  local pid; pid="$(cat "$pidfile" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Backend — FastAPI via uvicorn
# ---------------------------------------------------------------------------
if [ "$START_BACKEND" -eq 1 ]; then
  if is_running "$RUN_DIR/backend.pid"; then
    echo "⚠️  Backend already running (PID $(cat "$RUN_DIR/backend.pid")) on port $BACKEND_PORT."
  else
    echo "▶  Starting backend (FastAPI) on http://localhost:$BACKEND_PORT ..."
    if ! command -v python3 >/dev/null 2>&1; then
      echo "❌ python3 not found. Install Python 3 to run the backend."; exit 1
    fi
    # Enable the offline demo search backend so the Wellington retention agent
    # produces real (template-mode) recommendations without a live search API
    # or LLM configured. Set MEMBER_TRACKER_DEMO_SEARCH=0 before running to
    # disable. See backend/member_tracker/io/demo_search_backend.py.
    #
    # GEMINI_API_KEY (optional): when set, "Ask Wellington" answers via Google
    # Gemini, grounded in the live chapter data, and gracefully falls back to
    # the deterministic engine if the key is absent or the API fails. Run with:
    #   GEMINI_API_KEY=your-key ./start.sh
    ( cd "$BACKEND_DIR" \
        && MEMBER_TRACKER_DEMO_SEARCH="${MEMBER_TRACKER_DEMO_SEARCH:-1}" \
        exec python3 -m uvicorn member_tracker.api.app:app \
        --host 0.0.0.0 --port "$BACKEND_PORT" ) \
        > "$RUN_DIR/backend.log" 2>&1 &
    echo $! > "$RUN_DIR/backend.pid"
    echo "   backend PID $(cat "$RUN_DIR/backend.pid"), logs: $RUN_DIR/backend.log"
  fi
fi

# ---------------------------------------------------------------------------
# Frontend — Vite dev server
# ---------------------------------------------------------------------------
if [ "$START_FRONTEND" -eq 1 ]; then
  if is_running "$RUN_DIR/frontend.pid"; then
    echo "⚠️  Frontend already running (PID $(cat "$RUN_DIR/frontend.pid")) on port $FRONTEND_PORT."
  elif ! command -v npm >/dev/null 2>&1; then
    echo "⚠️  npm not found — skipping the dashboard."
    echo "    Install Node.js (e.g. 'brew install node' or https://nodejs.org),"
    echo "    then run: cd frontend && npm install && npm run dev"
  else
    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
      echo "▶  Installing frontend dependencies (first run) ..."
      ( cd "$FRONTEND_DIR" && npm install ) || {
        echo "❌ npm install failed."; exit 1; }
    fi
    echo "▶  Starting dashboard (Vite) on http://localhost:$FRONTEND_PORT ..."
    ( cd "$FRONTEND_DIR" && exec npm run dev -- --port "$FRONTEND_PORT" ) \
        > "$RUN_DIR/frontend.log" 2>&1 &
    echo $! > "$RUN_DIR/frontend.pid"
    echo "   frontend PID $(cat "$RUN_DIR/frontend.pid"), logs: $RUN_DIR/frontend.log"
  fi
fi

echo ""
echo "✅ Started. Open the dashboard at http://localhost:$FRONTEND_PORT"
echo "   Backend API:   http://localhost:$BACKEND_PORT   (docs at /docs)"
echo "   Health check:  curl http://localhost:$BACKEND_PORT/api/health"
echo "   Stop with:     ./stop.sh"
