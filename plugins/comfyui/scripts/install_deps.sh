#!/bin/bash
# Install ComfyUI core + custom-node Python deps into backend/venv.
#
# ComfyUI has NO separate plugin venv — it shares backend/venv/bin/python.
# Called from plugins/comfyui/scripts/start.sh on every start, and from
# scripts/heal_backend_venv.sh after a venv rebuild/restore.
#
# Env:
#   GUAARDVARK_HEAL_FORCE=1  — clear install stamps and reinstall everything
#   VENV_PYTHON                — override python path (default: backend/venv)

# Resolve this library's directory once (works sourced or executed directly).
_COMFYUI_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install_comfyui_python_deps() {
    local SCRIPT_DIR PLUGIN_ROOT PROJECT_ROOT COMFYUI_DIR
    SCRIPT_DIR="$_COMFYUI_SCRIPTS_DIR"
    PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
    COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
    VENV_PYTHON="${VENV_PYTHON:-$PROJECT_ROOT/backend/venv/bin/python}"

    if [ ! -f "$VENV_PYTHON" ]; then
        echo "Error: Python venv not found at $VENV_PYTHON" >&2
        return 1
    fi

    if [ ! -f "$COMFYUI_DIR/main.py" ]; then
        echo "Error: ComfyUI not found at $COMFYUI_DIR/main.py" >&2
        return 1
    fi

    local COMFYUI_REQS REQS_STAMP CN_DIR CN_STAMP
    COMFYUI_REQS="$COMFYUI_DIR/requirements.txt"
    REQS_STAMP="$PLUGIN_ROOT/.requirements_installed"
    CN_DIR="$COMFYUI_DIR/custom_nodes"
    CN_STAMP="$PLUGIN_ROOT/.custom_nodes_installed"

    if [ "${GUAARDVARK_HEAL_FORCE:-0}" = "1" ]; then
        echo "Force heal: clearing ComfyUI dependency stamps..."
        rm -f "$REQS_STAMP" "$CN_STAMP"
    fi

    # ComfyUI core requirements
    if [ -f "$COMFYUI_REQS" ]; then
        local REQS_HASH STAMP_HASH
        REQS_HASH=$(md5sum "$COMFYUI_REQS" 2>/dev/null | cut -d' ' -f1)
        STAMP_HASH=""
        [ -f "$REQS_STAMP" ] && STAMP_HASH=$(cat "$REQS_STAMP" 2>/dev/null)
        if [ "$REQS_HASH" != "$STAMP_HASH" ]; then
            echo "Installing ComfyUI requirements..."
            "$VENV_PYTHON" -m pip install -r "$COMFYUI_REQS" --quiet 2>&1 | tail -5
            echo "$REQS_HASH" > "$REQS_STAMP"
        fi

        local PINNED_FE INSTALLED_FE
        PINNED_FE=$(grep -E '^comfyui-frontend-package==' "$COMFYUI_REQS" 2>/dev/null | head -1 | cut -d= -f3 || true)
        if [ -n "$PINNED_FE" ]; then
            INSTALLED_FE=$("$VENV_PYTHON" -c "import comfyui_frontend_package as f; print(getattr(f,'__version__',''))" 2>/dev/null || true)
            if [ "$INSTALLED_FE" != "$PINNED_FE" ]; then
                echo "ComfyUI frontend drift ('$INSTALLED_FE' != '$PINNED_FE') — reinstalling..."
                "$VENV_PYTHON" -m pip install --quiet "comfyui-frontend-package==$PINNED_FE" 2>&1 | tail -3
            fi
        fi
    fi

    # facerestore_cf node + weights (video face-restore path)
    local FACERESTORE_DIR FR_MODELS_DIR
    FACERESTORE_DIR="$CN_DIR/facerestore_cf"
    if [ ! -f "$FACERESTORE_DIR/__init__.py" ]; then
        echo "Installing facerestore_cf custom node (face restore / CodeFormer)..."
        rm -rf "$FACERESTORE_DIR"
        git clone --depth 1 https://github.com/mav-rik/facerestore_cf.git "$FACERESTORE_DIR" 2>&1 | tail -3
    fi
    FR_MODELS_DIR="$COMFYUI_DIR/models/facerestore_models"
    mkdir -p "$FR_MODELS_DIR"
    if [ ! -f "$FR_MODELS_DIR/codeformer.pth" ]; then
        echo "Downloading codeformer.pth for face restore..."
        curl -fsSL -o "$FR_MODELS_DIR/codeformer.pth" \
            "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth" \
            || wget -q -O "$FR_MODELS_DIR/codeformer.pth" \
            "https://github.com/sczhou/CodeFormer/releases/download/v0.1.0/codeformer.pth"
    fi

    # All custom_nodes/*/requirements.txt → backend venv
    if [ -d "$CN_DIR" ]; then
        local CN_REQ_FILES CN_HASH CN_STAMP_HASH
        CN_REQ_FILES=$(find "$CN_DIR" -mindepth 2 -maxdepth 2 -name requirements.txt -type f 2>/dev/null | sort || true)
        if [ -n "$CN_REQ_FILES" ]; then
            CN_HASH=$(cat $CN_REQ_FILES 2>/dev/null | md5sum | cut -d' ' -f1 || true)
            CN_STAMP_HASH=""
            [ -f "$CN_STAMP" ] && CN_STAMP_HASH=$(cat "$CN_STAMP" 2>/dev/null)
            if [ -f "$CN_STAMP" ] && ! "$VENV_PYTHON" -c 'import cv2, gguf' >/dev/null 2>&1; then
                echo "Custom-node stamp present but video imports missing — reinstalling..."
                rm -f "$CN_STAMP"
                CN_STAMP_HASH=""
            fi
            if [ "$CN_HASH" != "$CN_STAMP_HASH" ]; then
                echo "Installing custom-node requirements..."
                set +e
                for req in $CN_REQ_FILES; do
                    local node_name
                    node_name=$(basename "$(dirname "$req")")
                    echo "  - $node_name"
                    "$VENV_PYTHON" -m pip install -r "$req" --quiet 2>&1 | tail -2
                    if [ $? -ne 0 ]; then
                        echo "    WARNING: pip install failed for $node_name (node may be disabled at runtime)."
                    fi
                done
                set -e
                echo "$CN_HASH" > "$CN_STAMP"
            fi
        fi
    fi

    # torchaudio/torchvision CUDA tag consistency (ComfyUI audio nodes)
    if ! "$VENV_PYTHON" -c 'import torchaudio' >/dev/null 2>&1; then
        local TORCH_CUDA TA_VER CH TV_VER REPIN
        TORCH_CUDA=$("$VENV_PYTHON" -c 'import torch; print((torch.version.cuda or "").replace(".",""))' 2>/dev/null || true)
        TA_VER=$("$VENV_PYTHON" -c 'import importlib.metadata as m,re; print(re.sub(r"\+.*","",m.version("torchaudio")))' 2>/dev/null || true)
        if [ -n "$TORCH_CUDA" ] && [ -n "$TA_VER" ]; then
            CH="cu${TORCH_CUDA}"
            TV_VER=$("$VENV_PYTHON" -c 'import importlib.metadata as m,re; print(re.sub(r"\+.*","",m.version("torchvision")))' 2>/dev/null || true)
            REPIN="torchaudio==${TA_VER}+${CH}"
            [ -n "$TV_VER" ] && REPIN="$REPIN torchvision==${TV_VER}+${CH}"
            echo "torch-family CUDA mismatch — re-pinning ($REPIN)..."
            "$VENV_PYTHON" -m pip install --no-deps --force-reinstall $REPIN \
                --index-url "https://download.pytorch.org/whl/${CH}" 2>&1 | tail -3
        else
            echo "WARNING: torchaudio import fails (cuda tag='$TORCH_CUDA', ver='$TA_VER') — audio nodes stay disabled (non-fatal)."
        fi
    fi

    # Video-critical deps (Wan GGUF + VHS encode)
    local VIDEO_DEPS_MISSING=()
    "$VENV_PYTHON" -c 'import cv2' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('opencv-python==4.8.1.78')
    "$VENV_PYTHON" -c 'import gguf' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('gguf>=0.13.0' 'sentencepiece' 'protobuf')
    "$VENV_PYTHON" -c 'import imageio_ffmpeg' >/dev/null 2>&1 || VIDEO_DEPS_MISSING+=('imageio-ffmpeg')
    if [ ${#VIDEO_DEPS_MISSING[@]} -gt 0 ]; then
        echo "Installing video-critical ComfyUI deps: ${VIDEO_DEPS_MISSING[*]}"
        "$VENV_PYTHON" -m pip install "${VIDEO_DEPS_MISSING[@]}" --quiet 2>&1 | tail -5
    fi

    # Common lightweight custom-node deps
    local OPTIONAL_CN_DEPS=(matplotlib scikit-image deepdiff lpips piexif)
    local OPTIONAL_MISSING=() pkg mod
    for pkg in "${OPTIONAL_CN_DEPS[@]}"; do
        mod="$pkg"
        [ "$pkg" = "scikit-image" ] && mod="skimage"
        "$VENV_PYTHON" -c "import ${mod}" >/dev/null 2>&1 || OPTIONAL_MISSING+=("$pkg")
    done
    if [ ${#OPTIONAL_MISSING[@]} -gt 0 ]; then
        echo "Installing common ComfyUI custom-node deps: ${OPTIONAL_MISSING[*]}"
        "$VENV_PYTHON" -m pip install "${OPTIONAL_MISSING[@]}" --quiet 2>&1 | tail -5
    fi

    # websocket-client — ComfyUI progress bridge + outreach scrapers (not always pulled by node reqs)
    if ! "$VENV_PYTHON" -c 'import websocket' >/dev/null 2>&1; then
        echo "Installing websocket-client..."
        "$VENV_PYTHON" -m pip install 'websocket-client==1.8.0' --quiet 2>&1 | tail -2
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    set -e
    install_comfyui_python_deps
fi
