#!/bin/bash
# Start Guaardvark Audio Foundry service.
# Matches the vision_pipeline / swarm plugin pattern: uvicorn, pid file, health wait.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
SERVICE_PORT=8206

# Load env from project root (if present)
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi
export GUAARDVARK_ROOT="$PROJECT_ROOT"

# Check if already running — idempotent re-start
PID_FILE="$PROJECT_ROOT/pids/audio_foundry.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Audio Foundry already running (PID: $OLD_PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

# Port conflict check — fail fast
if lsof -Pi :$SERVICE_PORT -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Error: Port $SERVICE_PORT is already in use"
    exit 1
fi

# Audio Foundry has TWO sibling venvs because chatterbox-tts and ACE-Step
# pin mutually-incompatible transformers versions (5.2 vs 4.50). Both also
# conflict with the main backend/venv (ComfyUI / vision_pipeline want
# torch 2.11, transformers <5). Two-venv split keeps everyone honest:
#   venv/        -> FastAPI dispatcher + voice_gen (chatterbox+kokoro) + audio_fx (SAO)
#   venv-music/  -> ACE-Step only; driven via subprocess from music_gen_acestep.py
ensure_venv() {
    local venv_dir="$1"
    local reqs_file="$2"
    local label="$3"

    # Cross-machine sync safety: venvs contain absolute shebangs + native bins/symlinks.
    # If the python inside doesn't exec or reports wrong root, nuke and recreate.
    venv_healthy() {
        local py="$venv_dir/bin/python"
        if [ ! -x "$py" ]; then
            return 1
        fi
        # Must run without error and its reported executable should live under this GX root (not master path).
        if ! "$py" -c 'import sys; print(sys.executable)' >/dev/null 2>&1; then
            return 1
        fi
        # Optional but strong: the printed path should contain current project (defensive vs old shebangs).
        if ! "$py" -c '
import sys
import os
root = os.environ.get("GUAARDVARK_ROOT", "")
exe = sys.executable
if root and root not in exe:
    # still allow if it at least runs; the recreate below is the real guard
    pass
print("ok")
' >/dev/null 2>&1; then
            :
        fi
        return 0
    }

    if [ ! -f "$venv_dir/bin/activate" ] || ! venv_healthy; then
        if [ -d "$venv_dir" ]; then
            echo "$label venv damaged / from another machine (bad shebang or missing python) — removing..."
            rm -rf "$venv_dir"
        fi
        echo "$label venv missing — bootstrapping at $venv_dir"
        python3 -m venv "$venv_dir" || { echo "Error: failed to create $label venv"; exit 1; }
        # shellcheck disable=SC1091
        source "$venv_dir/bin/activate"
        pip install --upgrade pip setuptools wheel
        pip install -r "$reqs_file" || { echo "Error: $label requirements install failed"; exit 1; }
        touch "$venv_dir/.deps_installed"
        deactivate
    else
        # shellcheck disable=SC1091
        source "$venv_dir/bin/activate"
        local sentinel="$venv_dir/.deps_installed"
        if [ ! -f "$sentinel" ] || [ "$reqs_file" -nt "$sentinel" ]; then
            echo "$label requirements changed — updating..."
            pip install -r "$reqs_file" || { echo "Error: $label requirements update failed"; exit 1; }
            touch "$sentinel"
        fi
        deactivate
    fi
}

PLUGIN_VENV="$PLUGIN_ROOT/venv"
MUSIC_VENV="$PLUGIN_ROOT/venv-music"

ensure_venv "$PLUGIN_VENV"  "$PLUGIN_ROOT/requirements.txt"        "audio_foundry"
ensure_venv "$MUSIC_VENV"   "$PLUGIN_ROOT/requirements-music.txt"  "audio_foundry-music"

# audio_fx (Stable Audio Open) needs diffusers >= 0.30, but chatterbox-tts
# pins diffusers == 0.29.0 in its setup.py. Listing both pins together in
# requirements.txt makes pip's strict resolver fail with ResolutionImpossible.
# So requirements.txt only has chatterbox; we do a forced upgrade pass here.
# pip prints a "dependency conflict" warning that is benign — chatterbox's
# actual usage is limited to scheduler classes that have been stable across
# diffusers 0.29 → 0.37.
DIFFUSERS_UPGRADE_SENTINEL="$PLUGIN_VENV/.diffusers_upgraded"
DIFFUSERS_REQUIRED='diffusers>=0.30,<0.40'
# Re-run the upgrade whenever requirements.txt has been edited (which would
# have just triggered a `pip install -r` that downgrades diffusers back to
# chatterbox's 0.29.0 pin). The sentinel lets idempotent restarts skip the
# step on cold-cache cases.
if [ ! -f "$DIFFUSERS_UPGRADE_SENTINEL" ] || [ "$PLUGIN_ROOT/requirements.txt" -nt "$DIFFUSERS_UPGRADE_SENTINEL" ]; then
    echo "Forcing diffusers upgrade for Stable Audio Open compatibility..."
    # shellcheck disable=SC1091
    source "$PLUGIN_VENV/bin/activate"
    pip install --upgrade "$DIFFUSERS_REQUIRED" || { echo "Error: diffusers upgrade failed"; exit 1; }
    touch "$DIFFUSERS_UPGRADE_SENTINEL"
    deactivate
fi

# Torch wheels that chatterbox-tts (and sometimes kokoro) transitively pull are
# often built for older GPUs only. On machines with brand-new Blackwell cards
# (RTX 50-series, compute capability 12.0 / sm_120) the stock wheel from
# `pip install chatterbox-tts==0.1.7` (torch 2.6 + cu124) produces:
#   "no kernel image is available for execution on the device"
# when doing .to("cuda") inside ChatterboxTTS.from_pretrained or KPipeline.
#
# The project-wide scripts/install_pytorch.sh already handles this by detecting
# nvidia-smi compute_cap and forcing the matching index (cu128 for Blackwell).
# We do the equivalent here for the *isolated* audio venv so it stays working
# even when the initial -r brings an ancient torch.
#
# Strategy (same pattern as the diffusers block):
#   - after the main requirements install
#   - detect the local GPU's cap (via nvidia-smi or torch if already importable)
#   - if the current torch build does not advertise a matching arch (sm_120 etc.)
#     or is obviously too old, force-reinstall the torch family from the
#     https://download.pytorch.org/whl/cu128 (or cu129/130) index using --no-deps
#     so chatterbox's strict "torch==2.6" metadata cannot downgrade us again.
#   - also ensure nvidia-cufile-cu12 (and friends) are present; some cu* wheels
#     dlopen them at import time even for pure inference.
# The sentinel is touched with the detected cap so a future GPU swap retriggers.
TORCH_COMPAT_SENTINEL="$PLUGIN_VENV/.torch_gpu_compat"
NEED_TORCH_COMPAT=0
if command -v nvidia-smi >/dev/null 2>&1; then
    CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')
    if [ -n "$CAP" ]; then
        # sm_120 (and future 12.x) require the newer cu128+ wheels that ship the kernels.
        MAJOR=${CAP%%.*}
        if [ "$MAJOR" -ge 12 ]; then
            # Probe the *current* torch (if importable) to see if it has the arch.
            if source "$PLUGIN_VENV/bin/activate" 2>/dev/null; then
                if python -c '
import torch, sys
al = torch.cuda.get_arch_list() if hasattr(torch.cuda, "get_arch_list") else []
if not any("sm_120" in str(a) or "sm_100" in str(a) for a in al):
    sys.exit(42)  # need upgrade
' 2>/dev/null; then
                    :
                else
                    NEED_TORCH_COMPAT=1
                fi
                deactivate 2>/dev/null || true
            else
                NEED_TORCH_COMPAT=1
            fi
        fi
    fi
fi
if [ "$NEED_TORCH_COMPAT" = "1" ] || [ ! -f "$TORCH_COMPAT_SENTINEL" ]; then
    echo "Forcing torch upgrade for Blackwell / sm_120 (RTX 50-series) compatibility..."
    # shellcheck disable=SC1091
    source "$PLUGIN_VENV/bin/activate"
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -1 || true
    pip install --upgrade --force-reinstall --no-deps torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128 || { echo "Error: torch cu128 install failed"; exit 1; }
    # Some cu128 wheels dynamically load these at import time even for inference.
    pip install --upgrade nvidia-cufile-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 2>&1 | tail -1 || true
    # Re-apply our other overrides that the resolver may have fought.
    pip install --upgrade "$DIFFUSERS_REQUIRED" 2>/dev/null || true
    touch "$TORCH_COMPAT_SENTINEL"
    deactivate
fi

# Activate the main venv for uvicorn — music venv is invoked on demand via
# subprocess by backends/music_gen_acestep.py.
# shellcheck disable=SC1091
source "$PLUGIN_VENV/bin/activate"

# Log setup
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/audio_foundry.log"

echo "Starting Audio Foundry..."
echo "Plugin dir: $PLUGIN_ROOT"
echo "Service port: $SERVICE_PORT"
echo "Log: $LOG_FILE"

cd "$PLUGIN_ROOT"
PYTHONPATH="$PLUGIN_ROOT:$PYTHONPATH" \
python -m uvicorn service.app:app --host 0.0.0.0 --port "$SERVICE_PORT" --workers 1 \
    >> "$LOG_FILE" 2>&1 &

PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$PID_DIR"
echo $! > "$PID_DIR/audio_foundry.pid"
echo "Audio Foundry started (PID: $(cat "$PID_DIR/audio_foundry.pid"))"

# Wait for health — generous window since first boot may download nothing heavy
# (all models load lazily) so this should normally be a few seconds.
echo "Waiting for health endpoint on port $SERVICE_PORT..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:$SERVICE_PORT/health" >/dev/null 2>&1; then
        echo "Audio Foundry health endpoint ready"
        exit 0
    fi
    sleep 1
done

echo "Warning: Health endpoint not responsive after 30s"
exit 0
