#!/bin/bash
# Start ComfyUI server

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
VENV_PYTHON="$PROJECT_ROOT/backend/venv/bin/python"
PORT=8188

# Check ComfyUI exists
if [ ! -f "$COMFYUI_DIR/main.py" ]; then
    echo "Error: ComfyUI not found at $COMFYUI_DIR/main.py"
    exit 1
fi

# Check if already running
if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ComfyUI is already running on port $PORT"
    exit 0
fi

# Check venv python exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Python venv not found at $VENV_PYTHON"
    exit 1
fi

# Install/update ComfyUI requirements (hash-based — catches submodule updates)
COMFYUI_REQS="$COMFYUI_DIR/requirements.txt"
REQS_STAMP="$PLUGIN_ROOT/.requirements_installed"
if [ -f "$COMFYUI_REQS" ]; then
    REQS_HASH=$(md5sum "$COMFYUI_REQS" 2>/dev/null | cut -d' ' -f1)
    STAMP_HASH=""
    [ -f "$REQS_STAMP" ] && STAMP_HASH=$(cat "$REQS_STAMP" 2>/dev/null)
    if [ "$REQS_HASH" != "$STAMP_HASH" ]; then
        echo "Installing ComfyUI requirements..."
        "$VENV_PYTHON" -m pip install -r "$COMFYUI_REQS" --quiet 2>&1 | tail -5
        echo "$REQS_HASH" > "$REQS_STAMP"
    fi

    # Self-heal the ComfyUI frontend package. After an Interconnector sync the
    # ComfyUI core can update to expect a newer comfyui-frontend-package than the
    # venv has — the md5 stamp above misses it when requirements.txt is byte-
    # identical but the *installed* package drifted — and ComfyUI throws the
    # "please reinstall comfyui-frontend-package" error at startup. Compare the
    # installed version to the pin and reinstall just that one package if off.
    PINNED_FE=$(grep -E '^comfyui-frontend-package==' "$COMFYUI_REQS" 2>/dev/null | head -1 | cut -d= -f3 || true)
    if [ -n "$PINNED_FE" ]; then
        INSTALLED_FE=$("$VENV_PYTHON" -c "import comfyui_frontend_package as f; print(getattr(f,'__version__',''))" 2>/dev/null || true)
        if [ "$INSTALLED_FE" != "$PINNED_FE" ]; then
            echo "ComfyUI frontend drift ('$INSTALLED_FE' != '$PINNED_FE') — reinstalling..."
            "$VENV_PYTHON" -m pip install --quiet "comfyui-frontend-package==$PINNED_FE" 2>&1 | tail -3
        fi
    fi
fi

# Install requirements for custom nodes. ComfyUI auto-loads every subdir under
# custom_nodes/, so any node that declares a requirements.txt needs its Python
# deps satisfied in the shared backend venv, otherwise you get noisy
# ModuleNotFoundError tracebacks at every ComfyUI startup (Impact-Pack → piexif,
# Crystools → deepdiff, PuLID/InstantID → insightface, etc.).
#
# We now discover *all* such requirements.txt (not just a video-only allowlist)
# so that commonly-installed nodes via ComfyUI-Manager (Impact, Crystools, etc.)
# get their easy deps. Heavier ones (insightface-based) will usually still fail
# to build here — we log a warning and continue; those nodes simply won't load.
# This is acceptable for music-video / film-crew paths (they don't depend on face
# analysis nodes). Hash-stamped over the *concat* of all req files so we only
# re-run when something actually changed.
CN_DIR="$COMFYUI_DIR/custom_nodes"
CN_STAMP="$PLUGIN_ROOT/.custom_nodes_installed"
if [ -d "$CN_DIR" ]; then
    CN_REQ_FILES=$(find "$CN_DIR" -mindepth 2 -maxdepth 2 -name requirements.txt -type f 2>/dev/null | sort || true)
    if [ -n "$CN_REQ_FILES" ]; then
        CN_HASH=$(cat $CN_REQ_FILES 2>/dev/null | md5sum | cut -d' ' -f1 || true)
        CN_STAMP_HASH=""
        [ -f "$CN_STAMP" ] && CN_STAMP_HASH=$(cat "$CN_STAMP" 2>/dev/null)
        if [ "$CN_HASH" != "$CN_STAMP_HASH" ]; then
            echo "Installing custom-node requirements (all nodes with requirements.txt)..."
            set +e
            for req in $CN_REQ_FILES; do
                node_name=$(basename "$(dirname "$req")")
                echo "  - $node_name"
                "$VENV_PYTHON" -m pip install -r "$req" --quiet 2>&1 | tail -2
                if [ $? -ne 0 ]; then
                    echo "    WARNING: pip install failed for $node_name (see above). Node will be disabled at runtime."
                fi
            done
            set -e
            echo "$CN_HASH" > "$CN_STAMP"
        fi
    fi
fi

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
