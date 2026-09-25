#!/usr/bin/env bash
# restart_celery.sh
#
# Kill and relaunch ONLY this checkout's Celery worker(s) and beat scheduler.
# The Flask backend, Postgres, Redis and the frontend are left untouched — the
# counterpart to restart_backend.sh for the other long-lived process.
#
# Why this exists: a Celery worker imports the backend package into a long-lived
# process that never reloads, so after backend Python changes it keeps executing
# the old code. restart_backend.sh does not touch workers (by design), and
# start_celery.sh deliberately refuses to start when workers are already running
# — so bouncing a worker had no single command. The symptom without one: workers
# logging ImportError for symbols that are present on disk.
#
# The launch is delegated to start_celery.sh, which owns the worker/beat flags,
# the broker wait, the beat-schedule cleanup and the liveness check.
#
# Usage: ./restart_celery.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/backend/venv"
PIDS_DIR="$SCRIPT_DIR/pids"
LOGS_DIR="$SCRIPT_DIR/logs"

if [ ! -x "$VENV_DIR/bin/celery" ]; then
  echo "Error: no venv celery at $VENV_DIR/bin/celery (run ./start.sh first)." >&2
  exit 1
fi

# ---- This checkout's Celery only ----------------------------------------------
# Matched on the real worker/beat CLI (never a parent shell that embeds the
# pattern in its argv) and then kept only when the process's working directory is
# under this repo: two installs on one machine otherwise see each other's workers
# as their own. Mirrors start_celery.sh's own_celery_pids, deliberately.
own_celery_pids() {  # $1 = worker | beat
  local pid cwd
  for pid in $(pgrep -f "celery -A backend.celery_app.celery $1" 2>/dev/null); do
    cwd=""
    if [ -e "/proc/$pid/cwd" ]; then
      cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null)
    elif command -v lsof >/dev/null 2>&1; then
      cwd=$(lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
    fi
    case "$cwd" in "$SCRIPT_DIR"|"$SCRIPT_DIR"/*) echo "$pid" ;; esac
  done
}

live_pids() {
  printf '%s\n%s\n' "$(own_celery_pids worker)" "$(own_celery_pids beat)" | sed '/^$/d' | sort -u
}

echo ">>> Restarting Celery for $SCRIPT_DIR"

# ---- Stop: SIGTERM (Celery's warm shutdown), then SIGKILL ---------------------
OLD_PIDS="$(live_pids)"
if [ -n "$OLD_PIDS" ]; then
  echo ">>> Stopping Celery (worker/beat PIDs: $(echo $OLD_PIDS | tr '\n' ' '))..."
  # SIGTERM is a warm shutdown: the worker finishes the task in flight, then
  # exits. A long GPU task can outlast the window below, in which case the
  # force-kill interrupts it — that is the intent, say so when it happens.
  for pid in $OLD_PIDS; do kill "$pid" 2>/dev/null || true; done
  for _ in $(seq 1 20); do
    [ -z "$(live_pids)" ] && break
    sleep 1
  done
  STILL="$(live_pids)"
  if [ -n "$STILL" ]; then
    echo ">>> Celery still up after 20s (a task may be mid-flight); force-killing: $(echo $STILL | tr '\n' ' ')"
    for pid in $STILL; do kill -9 "$pid" 2>/dev/null || true; done
    sleep 1
  fi
  # Stale pid files would outlive the processes they name; start_celery.sh
  # rewrites them and stop.sh sweeps them, but a stale one misleads both.
  rm -f "$PIDS_DIR"/celery_*.pid 2>/dev/null || true
else
  echo ">>> No Celery worker/beat running for this checkout"
fi

# ---- Launch, and verify something is actually serving -------------------------
echo ">>> Launching Celery via start_celery.sh..."
if ! bash "$SCRIPT_DIR/start_celery.sh"; then
  echo "!!! start_celery.sh failed — see $LOGS_DIR/celery_*.log" >&2
  exit 1
fi

NEW_WORKERS="$(own_celery_pids worker)"
NEW_BEAT="$(own_celery_pids beat)"
if [ -z "$NEW_WORKERS" ]; then
  echo "!!! No Celery worker running after the restart — see $LOGS_DIR/celery_*.log" >&2
  exit 1
fi

echo ">>> Celery restarted OK — workers: $(echo $NEW_WORKERS | tr '\n' ' ')  beat: ${NEW_BEAT:-none}"
echo ">>> Logs: $LOGS_DIR/celery_main.log  $LOGS_DIR/celery_training.log  $LOGS_DIR/celery_beat.log"
