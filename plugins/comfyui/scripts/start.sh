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

# Upstream leak (Comfy-Org/ComfyUI#13109, open as of 0.30.0): unpatch_model()
# clears self.backup but never self.patches, and offloaded weights hold a live
# reference to that dict — so on the lowvram path (any 16GB card running Wan)
# LoRA patch tensors accumulate in system RAM every generation until the OS
# locks up. Applied here, not just in the tree: ComfyUI/ is sync-excluded and
# restore_app.sh re-clones upstream, so a direct edit never reaches clients
# and doesn't survive a restore. Safe only together with --cache-none below
# (forces LoraLoader re-execution, so cleared patches are rebuilt every run).
"$VENV_PYTHON" - "$COMFYUI_DIR/comfy/model_patcher.py" <<'PYEOF'
import sys
path = sys.argv[1]
src = open(path).read()
if "self.patches.clear()" in src:
    sys.exit(0)  # already patched (or upstream fixed it)
needle = "            self.model.current_weight_patches_uuid = None\n            self.backup.clear()\n"
if src.count(needle) != 1:
    print("Warning: model_patcher.py layout changed; ComfyUI#13109 leak patch "
          "NOT applied — RAM will climb during video batches. Re-check the "
          "unpatch_model() fix against upstream.", file=sys.stderr)
    sys.exit(0)
src = src.replace(needle, needle + "            self.patches.clear()\n")
open(path, "w").write(src)
print("Applied ComfyUI#13109 leak patch (self.patches.clear() in unpatch_model)")
PYEOF

# Log file
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/comfyui.log"

echo "Starting ComfyUI..."
echo "Dir: $COMFYUI_DIR"
echo "Port: $PORT"
echo "Python: $VENV_PYTHON"
echo "Log: $LOG_FILE"

# Start ComfyUI. Memory flags (with the #13109 patch above, one package):
#   --disable-smart-memory  unload models after each run; RAM/VRAM exhaustion
#                           becomes a graceful OOM instead of an OS lockup
#   --cache-none            no execution cache: caps the RAM-pressure cache AND
#                           re-runs LoraLoader each prompt, which the patched
#                           unpatch_model() relies on to rebuild patches
#   --reserve-vram 1.0      the desktop compositor holds 600-800MB VRAM; without
#                           headroom a maxed 16GB card starves it and Wayland dies
cd "$COMFYUI_DIR"
# Under memory pressure the kernel must kill ComfyUI, never the desktop
# (2026-08-04 client box lockups). Children inherit; unprivileged raises allowed.
echo "${GUAARDVARK_OOM_SCORE_ADJ:-500}" > /proc/self/oom_score_adj 2>/dev/null || true
"$VENV_PYTHON" main.py --listen --port "$PORT" --disable-smart-memory --cache-none --reserve-vram 1.0 >> "$LOG_FILE" 2>&1 &

# Save PID
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/comfyui.pid"

echo "ComfyUI started (PID: $(cat $PID_DIR/comfyui.pid))"
