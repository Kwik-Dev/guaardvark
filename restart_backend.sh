#!/usr/bin/env bash
# restart_backend.sh
#
# Kill and relaunch ONLY the Guaardvark Flask backend, using the same launch
# command that start.sh uses. Postgres, Redis, Celery, and the frontend are left
# untouched. Primarily useful after editing backend Python code that the running
# process (python -m backend.app, no --debug reload) otherwise wouldn't pick up.
#
# Port resolution (first match wins):
#   1. $FLASK_PORT env var
#   2. FLASK_PORT=... in the repo-root .env
#   3. default 5000 (same fallback as start.sh)
#
# Usage: ./restart_backend.sh
#        FLASK_PORT=5055 ./restart_backend.sh   # force a specific port
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Resolve the backend port -------------------------------------------------
PORT="${FLASK_PORT:-}"
if [ -z "$PORT" ] && [ -f "$SCRIPT_DIR/.env" ]; then
  PORT="$(grep -E '^FLASK_PORT=' "$SCRIPT_DIR/.env" | tail -1 | cut -d= -f2- | tr -d '" ' || true)"
fi
PORT="${PORT:-5000}"

VENV_PY="$SCRIPT_DIR/backend/venv/bin/python"
LOG_FILE="$SCRIPT_DIR/logs/backend_startup.log"
LOG_DIR="$(dirname "$LOG_FILE")"
[ -d "$LOG_DIR" ] || mkdir -p "$LOG_DIR"

if [ ! -x "$VENV_PY" ]; then
  echo "Error: no venv python at $VENV_PY (run ./start.sh first)." >&2
  exit 1
fi

# ---- Helpers ------------------------------------------------------------------
health_ok() {
  curl -fsS -o /dev/null "http://localhost:$PORT/api/health" 2>/dev/null
}
listener_pid() {
  if command -v lsof >/dev/null 2>&1; then
    # Restrict to the actual LISTEN socket so we never mistake a client
    # connection on the same port (health check, etc.) for the backend.
    lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1
  elif command -v fuser >/dev/null 2>&1; then
    fuser "$PORT/tcp" 2>/dev/null
  else
    echo ""
  fi
}

echo ">>> Restarting Guaardvark backend on port $PORT"

# ---- Stop the current backend (graceful, then force) --------------------------
OLD_PID="$(listener_pid)"
if [ -n "$OLD_PID" ]; then
  echo ">>> Stopping current backend (PID $OLD_PID)..."
  kill "$OLD_PID" 2>/dev/null || true
  for _ in $(seq 1 12); do
    [ -z "$(listener_pid)" ] && break
    sleep 1
  done
  STILL="$(listener_pid)"
  if [ -n "$STILL" ]; then
    echo ">>> Backend not stopping, force-killing PID $STILL..."
    kill -9 "$STILL" 2>/dev/null || true
    sleep 1
  fi
else
  echo ">>> No backend currently listening on :$PORT"
fi

# Sweep any lingering `python -m backend.app` for this install (e.g. mid-startup,
# not yet holding the port). Restrict the match to this repo's venv so we never
# touch a foreign process.
pkill -f "$SCRIPT_DIR/backend/.+ -m backend.app" 2>/dev/null || true
pkill -f " -m backend.app" 2>/dev/null || true
sleep 1

# ---- Launch exactly like start.sh ---------------------------------------------
echo ">>> Launching backend..."
nohup env GUAARDVARK_ROOT="$SCRIPT_DIR" FLASK_PORT="$PORT" \
     GUAARDVARK_MIGRATIONS_VERIFIED="${GUAARDVARK_MIGRATIONS_VERIFIED:-}" \
     "$VENV_PY" -m backend.app >> "$LOG_FILE" 2>&1 &

# ---- Wait for health ------------------------------------------------------------
echo ">>> Waiting for backend to become healthy on :$PORT..."
SUCCESS=0
for _ in $(seq 1 90); do
  if health_ok; then SUCCESS=1; break; fi
  sleep 1
done

NEW_PID="$(listener_pid)"
if [ -n "$NEW_PID" ]; then
  mkdir -p "$SCRIPT_DIR/pids"
  echo "$NEW_PID" > "$SCRIPT_DIR/pids/backend.pid" 2>/dev/null || true
fi

if [ "$SUCCESS" -eq 1 ]; then
  echo ">>> Backend restarted OK -> http://localhost:$PORT (PID ${NEW_PID:-unknown})"
  exit 0
else
  echo "!!! Backend did not become healthy on :$PORT after 90s. Check $LOG_FILE" >&2
  exit 1
fi
