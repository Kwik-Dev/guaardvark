#!/bin/bash
# Heal backend/venv after a rebuild, restore, or incomplete pip install.
#
# ComfyUI shares this venv (no plugins/comfyui/venv). Run this after any venv
# wipe, then restart the backend + ComfyUI (or use --restart-comfyui).
#
# Usage:
#   ./scripts/heal_backend_venv.sh                 # full heal
#   ./scripts/heal_backend_venv.sh --skip-cv       # skip requirements-cv.txt
#   ./scripts/heal_backend_venv.sh --comfyui-only    # ComfyUI deps only (fast)
#   ./scripts/heal_backend_venv.sh --no-restart      # don't bounce ComfyUI
#
# Logs: logs/heal_backend_venv.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
VENV_DIR="$BACKEND_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/heal_backend_venv.log"

SKIP_CV=0
COMFYUI_ONLY=0
RESTART_COMFYUI=1

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-cv) SKIP_CV=1 ;;
        --comfyui-only) COMFYUI_ONLY=1 ;;
        --no-restart) RESTART_COMFYUI=0 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
    shift
done

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

repin_numpy_setuptools() {
    log "Re-pinning numpy<2 and setuptools (ML stack guard)..."
    "$VENV_PIP" install --no-deps --force-reinstall \
        'numpy<2.0,>=1.26.4' 'setuptools>=80.9.0,<81' 2>&1 | tail -3 || true
    # Keep opencv on the project pin if CV deps bumped it to 5.x
    if "$VENV_PYTHON" -c 'import cv2' >/dev/null 2>&1; then
        "$VENV_PIP" install 'opencv-python==4.8.1.78' --quiet 2>&1 | tail -2 || true
    fi
}

heal_backend_core() {
    log "=== Step 1: backend core requirements ==="
    if [ -f "$BACKEND_DIR/requirements-base.txt" ]; then
        "$VENV_PIP" install -r "$BACKEND_DIR/requirements-base.txt" 2>&1 | tail -5
    fi
    if [ -f "$BACKEND_DIR/requirements.txt" ]; then
        "$VENV_PIP" install -r "$BACKEND_DIR/requirements.txt" 2>&1 | tail -5
    fi
    # Packages reconciler verifies but pip resolution sometimes drops
    "$VENV_PIP" install 'websocket-client==1.8.0' --quiet 2>&1 | tail -2 || true
}

heal_pytorch() {
    log "=== Step 2: PyTorch + CUDA family ==="
    if [ -f "$REPO_ROOT/scripts/install_pytorch.sh" ]; then
        GUAARDVARK_TORCH_CHANNEL="$("$VENV_PYTHON" -m backend.services.hardware_policy torch_channel 2>/dev/null || true)" \
            bash "$REPO_ROOT/scripts/install_pytorch.sh" 2>&1 | tail -8 || log "WARNING: install_pytorch.sh exited non-zero"
    fi
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        "$VENV_PIP" uninstall -y nvidia-ml-py pynvml 2>/dev/null | tail -1 || true
    else
        "$VENV_PIP" install nvidia-ml-py --quiet 2>&1 | tail -2 || true
    fi
    repin_numpy_setuptools
    "$VENV_PIP" uninstall -y flash-attn flash_attn xformers 2>/dev/null | tail -1 || true
}

heal_dep_reconciler() {
    log "=== Step 3: dep reconciler (backend_venv + cli_venv) ==="
    if [ -x "$REPO_ROOT/scripts/dep_reconciler.py" ] || [ -f "$REPO_ROOT/scripts/dep_reconciler.py" ]; then
        python3 "$REPO_ROOT/scripts/dep_reconciler.py" \
            --force --only backend_venv,cli_venv --repo-root "$REPO_ROOT" 2>&1 | tail -15 \
            || log "WARNING: dep_reconciler reported issues (see logs/dep_reconciler.log)"
    fi
    repin_numpy_setuptools
}

heal_cv_optional() {
    if [ "$SKIP_CV" -eq 1 ]; then
        log "Skipping requirements-cv.txt (--skip-cv)"
        return 0
    fi
    local arch want=0
    arch="$(uname -m 2>/dev/null || echo unknown)"
    if [ "${GUAARDVARK_INSTALL_CV:-0}" = "1" ]; then
        want=1
    elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 \
         && [ "$arch" != "aarch64" ] && [ "$arch" != "arm64" ]; then
        want=1
    fi
    if [ "$want" -ne 1 ]; then
        log "Skipping requirements-cv.txt (no NVIDIA GPU or ARM). Force with GUAARDVARK_INSTALL_CV=1"
        return 0
    fi
    log "=== Step 4: optional CV / face restoration (requirements-cv.txt) ==="
    if [ -f "$BACKEND_DIR/requirements-cv.txt" ]; then
        set +e
        "$VENV_PIP" install -r "$BACKEND_DIR/requirements-cv.txt" 2>&1 | tail -10
        local rc=$?
        set -e
        repin_numpy_setuptools
        if [ "$rc" -ne 0 ]; then
            log "WARNING: requirements-cv.txt install had errors (face-restore may stay disabled)"
        fi
    fi
}

heal_comfyui_deps() {
    log "=== Step 5: ComfyUI + custom-node deps (backend venv) ==="
    export GUAARDVARK_HEAL_FORCE=1
    export VENV_PYTHON
    bash "$REPO_ROOT/plugins/comfyui/scripts/install_deps.sh"
    repin_numpy_setuptools
}

verify_heal() {
    log "=== Step 6: verification ==="
    local failed=0
    "$VENV_PYTHON" -c "
import sys
checks = [
    ('numpy 1.x', 'import numpy; assert numpy.__version__.startswith(\"1.\")'),
    ('torch', 'import torch'),
    ('flask', 'import flask'),
    ('celery', 'import celery'),
    ('cv2', 'import cv2'),
    ('gguf', 'import gguf'),
    ('websocket', 'import websocket'),
]
for label, stmt in checks:
    try:
        exec(stmt)
        print(f'  OK  {label}')
    except Exception as e:
        print(f'  FAIL {label}: {e}', file=sys.stderr)
        sys.exit(1)
" || failed=1

    if command -v curl >/dev/null 2>&1 && curl -sf http://127.0.0.1:8188/ >/dev/null 2>&1; then
        log "ComfyUI is up — checking critical nodes..."
        "$VENV_PYTHON" -c "
import requests
d = requests.get('http://127.0.0.1:8188/object_info', timeout=10).json()
for n in ('UnetLoaderGGUF', 'VHS_VideoCombine', 'RIFE VFI'):
    ok = n in d
    print(f'  {\"OK\" if ok else \"FAIL\"}  ComfyUI node {n}')
    if not ok:
        raise SystemExit(1)
" || failed=1
    else
        log "ComfyUI not running — skip live node check (restart ComfyUI after heal)"
    fi

    if [ "$failed" -ne 0 ]; then
        log "Verification FAILED — see $LOG_FILE"
        return 1
    fi
    log "Verification passed"
    return 0
}

restart_comfyui_if_requested() {
    if [ "$RESTART_COMFYUI" -ne 1 ]; then
        log "Skipping ComfyUI restart (--no-restart)"
        return 0
    fi
    log "=== Step 7: restart ComfyUI ==="
    if [ -x "$REPO_ROOT/plugins/comfyui/scripts/stop.sh" ]; then
        bash "$REPO_ROOT/plugins/comfyui/scripts/stop.sh" || true
        sleep 2
    fi
    if [ -x "$REPO_ROOT/plugins/comfyui/scripts/start.sh" ]; then
        bash "$REPO_ROOT/plugins/comfyui/scripts/start.sh"
    fi
}

main() {
    log "========== heal_backend_venv START (repo: $REPO_ROOT) =========="

    if [ ! -x "$VENV_PYTHON" ]; then
        log "ERROR: $VENV_PYTHON not found."
        log "Create the venv first (e.g. ./start.sh or system-manager repair), then re-run this script."
        exit 1
    fi

    if [ "$COMFYUI_ONLY" -eq 1 ]; then
        heal_comfyui_deps
        restart_comfyui_if_requested
        verify_heal || exit 1
        log "========== heal_backend_venv DONE (comfyui-only) =========="
        exit 0
    fi

    heal_backend_core
    heal_pytorch
    heal_dep_reconciler
    heal_cv_optional
    heal_comfyui_deps
    restart_comfyui_if_requested
    verify_heal || exit 1

    log "========== heal_backend_venv DONE =========="
    log "Restart the Flask backend if it is running (./start.sh or restart backend service)."
}

main "$@"
