#!/bin/bash
# Start ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
VENV_PYTHON="$PROJECT_ROOT/backend/venv/bin/python"
PORT=8188

# Errors go to stderr so plugin_manager can surface them (it used to only
# show stderr; blank UI failures were start.sh writing only to stdout).
err() { echo "Error: $*" >&2; }

# Check ComfyUI app tree exists
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    err "ComfyUI not found at $COMFYUI_DIR/main.py"
    err "Restore the app tree (preserves models/): bash $SCRIPT_DIR/restore_app.sh"
    exit 1
fi

# Incomplete clone/rsync can drop nested package comfy/ldm/models (not the
# top-level weights tree). Detect early with a clear restore hint.
if [ ! -f "$COMFYUI_DIR/comfy/ldm/models/autoencoder.py" ]; then
    err "Incomplete ComfyUI install: missing comfy/ldm/models/autoencoder.py"
    err "A bad rsync --exclude 'models' can delete nested packages. Fix with:"
    err "  bash $SCRIPT_DIR/restore_app.sh"
    exit 1
fi

# Check if already running
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ComfyUI is already running on port $PORT"
    exit 0
fi

# Check venv python exists
if [ ! -f "$VENV_PYTHON" ]; then
    err "Python venv not found at $VENV_PYTHON"
    exit 1
fi

# Install ComfyUI + custom-node deps into backend/venv (shared — no plugin venv)
# shellcheck source=install_deps.sh
source "$SCRIPT_DIR/install_deps.sh"
install_comfyui_python_deps

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui.log"

echo "Starting ComfyUI..."
echo "Dir: $COMFYUI_DIR"
echo "Port: $PORT"
echo "Python: $VENV_PYTHON"
echo "Log: $LOG_FILE"

# Start ComfyUI
cd "$COMFYUI_DIR"
"$VENV_PYTHON" main.py --listen --port "$PORT" >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/comfyui.pid"

echo "ComfyUI started (PID: $(cat $PID_DIR/comfyui.pid))"
