#!/bin/bash
# scripts/install_pytorch.sh
# Smart PyTorch installer that detects GPU and installs correct CUDA version

set -e

# Colors for output (matching Vader theme from start.sh)
VADER_RED="\033[38;5;196m"       # #d32f2f - primary red
VADER_RED_DARK="\033[38;5;88m"   # #b71c1c - dark red
VADER_RED_LIGHT="\033[38;5;203m" # #f44336 - light red
VADER_GRAY="\033[38;5;244m"      # Lighter gray for better visibility
VADER_GRAY_DARK="\033[38;5;238m" # Dark gray
VADER_WHITE="\033[38;5;255m"     # Pure white
VADER_WHITE_DIM="\033[38;5;250m" # Dim white
VADER_RESET="\033[0m"
VADER_BOLD="\033[1m"

# Output helpers
vader_header() { echo -e "\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}\n${VADER_WHITE}${VADER_BOLD}  $1${VADER_RESET}\n${VADER_RED}${VADER_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${VADER_RESET}"; }
vader_info() { echo -e "  ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_success() { echo -e "  ${VADER_RED}✔${VADER_RESET} ${VADER_WHITE}$1${VADER_RESET}"; }
vader_warn() { echo -e "  ${VADER_RED_LIGHT}⚠${VADER_RESET} ${VADER_RED_LIGHT}$1${VADER_RESET}"; }
vader_detail() { echo -e "    ${VADER_GRAY}·${VADER_RESET} ${VADER_WHITE_DIM}$1${VADER_RESET}"; }
vader_section() { echo -e "\n${VADER_RED}${VADER_BOLD}► $1${VADER_RESET}"; }

vader_header "PyTorch Smart Installer"

# Venv safety: detect the project's venv and use its pip explicitly.
# Without this, running this script directly (not via start.sh) resolves
# pip to the system Python, which on modern Debian/Ubuntu triggers the
# PEP 668 "externally-managed-environment" error. start.sh activates the
# venv before calling us, so in that path nothing changes — but direct
# invocation now works too.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Use a dedicated tmp for large CUDA wheels (avoids ENOSPC on small /tmp tmpfs
# such as 8 GB tmpfs on some boxes). Respect an explicit TMPDIR if the caller
# already set one. See 2026-06-14 hardware provisioning notes.
if [ -z "${TMPDIR:-}" ]; then
    PIP_TMP="$PROJECT_ROOT/data/piptmp"
    mkdir -p "$PIP_TMP" 2>/dev/null || true
    if [ -d "$PIP_TMP" ] && [ -w "$PIP_TMP" ]; then
        export TMPDIR="$PIP_TMP"
        export PIP_CACHE_DIR="$PIP_TMP"
    fi
fi

# --venv <path> (or TARGET_VENV env) selects which venv to install into.
# Defaults to the backend venv, preserving all existing call sites.
TARGET_VENV="${TARGET_VENV:-$PROJECT_ROOT/backend/venv}"
while [ $# -gt 0 ]; do
    case "$1" in
        --venv)
            if [ -z "${2:-}" ]; then
                vader_warn "--venv requires a path argument"; exit 1
            fi
            TARGET_VENV="$2"; shift 2 ;;
        --venv=*) TARGET_VENV="${1#*=}"; shift ;;
        *) shift ;;
    esac
done

VENV_PIP="$TARGET_VENV/bin/pip"
VENV_PYTHON="$TARGET_VENV/bin/python"

if [ -x "$VENV_PIP" ] && [ -x "$VENV_PYTHON" ]; then
    vader_info "Using venv: $TARGET_VENV"
    pip() { "$VENV_PIP" "$@"; }
    python3() { "$VENV_PYTHON" "$@"; }
else
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        vader_warn "No venv at $TARGET_VENV AND no active virtualenv."
        vader_warn "Refusing to install torch into system Python. Activate a venv first, or"
        vader_warn "create it with: python3 -m venv $TARGET_VENV"
        exit 1
    fi
    vader_info "Using active virtualenv: $VIRTUAL_ENV"
fi

# ---------------------------------------------------------------------------
# Accelerator branching.
#
# Historically this installer branched ONLY on `nvidia-smi`: every non-NVIDIA
# host (AMD ROCm, Apple Silicon, plain CPU) got the whl/cpu wheel. That meant
# AMD boxes ran torch on the CPU and Macs never got MPS. We now branch FIRST on
# the two previously-missing accelerators (Apple Metal, AMD ROCm); if neither
# applies we fall through to the original NVIDIA-or-CPU logic UNCHANGED.
#
# Detection order is deliberate:
#   1. Darwin (uname)         -> default PyPI wheel (MPS-capable; never cpu URL)
#   2. AMD ROCm (rocm-smi /   -> whl/rocmX.Y  (version overridable via env)
#      hardware.json vendor)
#   3. NVIDIA (nvidia-smi)    -> existing CUDA-arch logic (unchanged)
#   4. anything else / failed -> existing whl/cpu fallback (unchanged)
#
# The ROCm wheel index version is overridable so a host on a newer/older ROCm
# runtime can pin it without editing this script:
#     GUAARDVARK_ROCM_WHL=rocm6.2 bash scripts/install_pytorch.sh
ROCM_WHL="${GUAARDVARK_ROCM_WHL:-rocm6.3}"
HARDWARE_JSON="${GUAARDVARK_HARDWARE_JSON:-$HOME/.guaardvark/hardware.json}"

# --- helper: does hardware.json report an AMD GPU? -------------------------
# hardware_detector.py writes {"gpu": {"vendor": "amd", ...}}. We treat that as
# a secondary AMD signal in case rocm-smi isn't on PATH yet (fresh provision).
# Pure text probe (no python/jq dependency) so it works before the venv exists.
_hardware_json_says_amd() {
    [ -f "$HARDWARE_JSON" ] || return 1
    grep -q '"vendor"[[:space:]]*:[[:space:]]*"amd"' "$HARDWARE_JSON" 2>/dev/null
}

UNAME_S="$(uname -s 2>/dev/null || echo unknown)"

# === Branch 1: Apple Silicon / Intel Mac (Metal/MPS) =======================
if [ "$UNAME_S" = "Darwin" ]; then
    vader_success "macOS (Darwin) detected"
    vader_section "Accelerator: Apple Metal (MPS)"
    vader_detail "Platform:      $(uname -m 2>/dev/null || echo unknown)"
    vader_detail "PyTorch Index: default PyPI (MPS-capable wheel)"
    vader_detail "Note:          NOT using the whl/cpu index — that wheel has no MPS."
    echo ""
    vader_info "Installing default PyTorch (MPS where the OS/GPU supports it)..."
    echo ""
    # Mac: do NOT pass an --index-url. The default PyPI macOS wheel is the
    # MPS-capable build; the whl/cpu index would strip Metal support. Swap-safety
    # uninstall first (same rationale as the other branches) but no CUDA/triton
    # cleanup — those never exist on macOS — and no pynvml removal.
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    pip install --upgrade --force-reinstall torch torchvision torchaudio

    vader_section "Verification:"
    python3 << 'EOF'
import torch
print(f"    PyTorch Version:    {torch.__version__}")
mps = getattr(torch.backends, "mps", None)
avail = bool(mps and mps.is_available())
print(f"    MPS Available:      {avail}")
try:
    dev = "mps" if avail else "cpu"
    t = torch.zeros(1, device=dev)
    print(f"    {dev.upper()} Tensor Test:    PASSED")
except Exception as e:
    print(f"    Tensor Test:        FAILED ({e})")
    # Fall back to a CPU tensor so the verification still proves torch works.
    try:
        torch.zeros(1)
        print("    CPU Tensor Test:    PASSED")
    except Exception as e2:
        print(f"    CPU Tensor Test:    FAILED ({e2})")
EOF

    vader_header "PyTorch Installation Complete"
    exit 0
fi

# === Branch 2: AMD ROCm ====================================================
# rocm-smi on PATH is the primary signal; hardware.json vendor=="amd" is the
# fallback. We intentionally do NOT trigger ROCm just because nvidia-smi is
# absent — that would regress the CPU path for non-AMD machines.
if command -v rocm-smi &> /dev/null || _hardware_json_says_amd; then
    if command -v rocm-smi &> /dev/null; then
        vader_success "AMD ROCm runtime detected (rocm-smi)"
    else
        vader_success "AMD GPU detected (hardware.json vendor=amd)"
    fi
    vader_section "Accelerator: AMD ROCm"
    vader_detail "Platform:       $(uname -m 2>/dev/null || echo unknown)"
    vader_detail "PyTorch Index:  https://download.pytorch.org/whl/${ROCM_WHL}"
    vader_detail "ROCm wheel:     ${ROCM_WHL} (override with GUAARDVARK_ROCM_WHL)"
    echo ""
    vader_info "Installing PyTorch with ROCm (${ROCM_WHL}) support..."
    echo ""
    # Swap-safety: clean prior torch + any lingering CUDA/triton bloat from a
    # previous build, then force-reinstall the ROCm variant (the +rocm local
    # tag collides with +cpu/+cuXXX in pip's resolver, same as the CUDA path).
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
    # Purge flash-attn/xformers/pynvml for the same reasons as CUDA/CPU paths (shared
    # backend/venv contract; custom nodes and some plugin reqs inject incompatible
    # versions leading to diffusers import crashes with aten schema errors).
    pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true
    pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/${ROCM_WHL}"

    vader_section "Verification:"
    python3 << 'EOF'
import torch
print(f"    PyTorch Version:    {torch.__version__}")
# ROCm torch reports through the CUDA API surface (torch.cuda.is_available()
# is True, torch.version.hip is set). Report both so a misbuild is obvious.
print(f"    HIP Version:        {getattr(torch.version, 'hip', None)}")
print(f"    GPU Available:      {torch.cuda.is_available()}")
if torch.cuda.is_available():
    try:
        print(f"    GPU Device:         {torch.cuda.get_device_name(0)}")
    except Exception as e:
        print(f"    GPU Device:         N/A ({e})")
    try:
        torch.zeros(1).cuda()
        print("    GPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    GPU Tensor Test:    FAILED ({e})")
else:
    print("    Mode:               CPU-only (ROCm wheel installed but GPU not visible)")
    try:
        torch.zeros(1)
        print("    CPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    CPU Tensor Test:    FAILED ({e})")
EOF

    vader_header "PyTorch Installation Complete"
    exit 0
fi

# === Branch 3 + 4: NVIDIA (CUDA arch logic) or CPU fallback ================
# Everything below is the ORIGINAL installer, unchanged. Reached only when the
# host is not macOS and not AMD/ROCm.
# Detect if NVIDIA GPU is present
if command -v nvidia-smi &> /dev/null; then
    vader_success "NVIDIA driver detected"

    # Get comprehensive GPU information
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    COMPUTE_CAP=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1)
    DRIVER_VERSION=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEMORY=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)

    vader_section "GPU Information:"
    vader_detail "GPU Model:          ${GPU_NAME:-Unknown}"
    vader_detail "Compute Capability: ${COMPUTE_CAP:-Unknown}"
    vader_detail "Driver Version:     ${DRIVER_VERSION:-Unknown}"
    vader_detail "GPU Memory:         ${GPU_MEMORY:-Unknown}"

    if [ -n "$COMPUTE_CAP" ]; then
        # Convert compute capability to major version (e.g., "8.9" -> "8")
        COMPUTE_MAJOR=$(echo "$COMPUTE_CAP" | cut -d. -f1)
        COMPUTE_MINOR=$(echo "$COMPUTE_CAP" | cut -d. -f2)

        # If the caller resolved the channel from hardware_policy (single source
        # of truth), honor it and skip the built-in table below.
        if [ -n "${GUAARDVARK_TORCH_CHANNEL:-}" ]; then
            CUDA_VERSION="$GUAARDVARK_TORCH_CHANNEL"
            CUDA_NAME="$GUAARDVARK_TORCH_CHANNEL"
            ARCH_NAME="policy(${GUAARDVARK_TORCH_CHANNEL})"
            vader_info "Torch channel from hardware_policy: $CUDA_VERSION"
        fi

        # Determine which CUDA version to use with detailed explanation
        vader_section "Architecture Detection:"

        if [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ]; then
        if [ "$COMPUTE_MAJOR" -ge 12 ]; then
            CUDA_VERSION="cu128"
            CUDA_NAME="12.8"
            ARCH_NAME="Blackwell"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for sm_120 kernel support"
        elif [ "$COMPUTE_MAJOR" -ge 9 ]; then
            CUDA_VERSION="cu128"
            CUDA_NAME="12.8"
            ARCH_NAME="Hopper"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for optimal performance"
        elif [ "$COMPUTE_MAJOR" -ge 8 ]; then
            CUDA_VERSION="cu121"
            CUDA_NAME="12.1"
            ARCH_NAME="Ampere/Ada Lovelace"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for modern GPU support"
        elif [ "$COMPUTE_MAJOR" -ge 7 ]; then
            CUDA_VERSION="cu118"
            CUDA_NAME="11.8"
            ARCH_NAME="Volta/Turing"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for compatibility"
        elif [ "$COMPUTE_MAJOR" -ge 6 ]; then
            CUDA_VERSION="cu118"
            CUDA_NAME="11.8"
            ARCH_NAME="Pascal"
            vader_info "Detected ${ARCH_NAME} architecture (compute ${COMPUTE_CAP})"
            vader_detail "Using CUDA ${CUDA_NAME} for legacy GPU support"
        else
            CUDA_VERSION="cpu"
            CUDA_NAME="CPU-only"
            ARCH_NAME="Legacy (pre-Pascal)"
            vader_warn "GPU compute capability ${COMPUTE_CAP} is too old for CUDA support"
            vader_detail "Falling back to CPU-only mode"
        fi
        fi  # end: [ -z "${GUAARDVARK_TORCH_CHANNEL:-}" ]

        vader_section "Installation Plan:"

        # --force-reinstall is required because pip's resolver treats the
        # local-version tag (e.g. +cu130 vs +cpu) as the SAME version number
        # for "already satisfied" purposes. Without --force-reinstall, a machine
        # restored from a GPU host's backup will report success but keep the
        # wrong variant. Also uninstall any lingering CUDA/triton deps that
        # were pulled in by a previous GPU build so we don't carry dead weight.
        vader_section "Cleaning prior torch variants and CUDA dependency bloat..."
        # IMPORTANT: This uninstall step runs *before* the reinstall. If you
        # Ctrl-C during this phase the target venv will be left without torch
        # (and without the old one either). Let the script finish. The verify
        # gate at the end of start.sh will surface the problem if it happens.
        pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
        pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
        # Purge flash-attn / xformers (the #1 source of "Current Torch with Flash-Attention 2.5.7
        # doesnt have a compatible aten::_flash_attention_forward schema (philox_seed vs rng_state)"
        # errors on diffusers import in batch_image_generation_api / offline_image_generator).
        # Also purge pynvml/nvidia-ml-py (FutureWarning on every torch.cuda touch; re-pulled by
        # plugin reqs like upscaling/vision into the shared backend/venv). These are optional
        # accelerators; core diffusers/Comfy paths degrade gracefully without them.
        pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true

        if [ "$CUDA_VERSION" != "cpu" ]; then
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/${CUDA_VERSION}"
            vader_detail "CUDA Version:  ${CUDA_NAME}"
            vader_detail "Target Arch:   ${ARCH_NAME}"
            echo ""
            vader_info "Installing PyTorch with CUDA ${CUDA_NAME} support..."
            echo ""
            pip install --upgrade --force-reinstall ${USE_PRE:-}torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/$CUDA_VERSION"
        else
            vader_detail "PyTorch Index: https://download.pytorch.org/whl/cpu"
            vader_detail "Mode:          CPU-only (GPU not supported)"
            echo ""
            vader_info "Installing CPU-only PyTorch..."
            echo ""
            pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
            # pynvml is deprecated and fires FutureWarning on every `import torch`
            # via torch/cuda/__init__.py. On CPU-only hosts it serves no purpose —
            # torch handles the ImportError gracefully. Remove it to silence the noise.
            pip uninstall -y pynvml 2>/dev/null | tail -2 || true
        fi

        # Verification
        vader_section "Verification:"
        python3 << 'EOF'
import torch

# Basic info
print(f"    PyTorch Version:    {torch.__version__}")
print(f"    CUDA Available:     {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"    CUDA Version:       {torch.version.cuda}")
    try:
        print(f"    cuDNN Version:      {torch.backends.cudnn.version()}")
    except:
        print(f"    cuDNN Version:      N/A")
    print(f"    GPU Device:         {torch.cuda.get_device_name(0)}")
    cap = torch.cuda.get_device_capability(0)
    print(f"    Compute Capability: {cap[0]}.{cap[1]}")

    # Quick tensor test
    try:
        test_tensor = torch.zeros(1).cuda()
        print(f"    GPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    GPU Tensor Test:    FAILED ({e})")
else:
    print("    Mode:               CPU-only")

    # Quick CPU test
    try:
        test_tensor = torch.zeros(1)
        print(f"    CPU Tensor Test:    PASSED")
    except Exception as e:
        print(f"    CPU Tensor Test:    FAILED ({e})")
EOF

    else
        vader_warn "Could not detect GPU compute capability"
        vader_info "Installing CPU-only PyTorch as fallback..."
        echo ""
        pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
        pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
        pip uninstall -y pynvml flash-attn flash_attn xformers nvidia-ml-py 2>/dev/null | tail -2 || true

        vader_section "Verification:"
        python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
    fi
else
    vader_section "GPU Detection:"
    vader_detail "nvidia-smi:     Not found"
    vader_detail "CUDA Support:   Not available"
    echo ""
    vader_info "Installing CPU-only PyTorch..."
    echo ""
    # Same variant-swap safety: uninstall first, force-reinstall, drop pynvml.
    pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
    pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true
    # Also purge flash/xformers/pynvml here (see main CUDA clean comment for rationale:
    # prevents schema mismatch on diffusers import + repeated FutureWarnings from plugins).
    pip uninstall -y flash-attn flash_attn xformers pynvml nvidia-ml-py 2>/dev/null | tail -3 || true
    pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

    vader_section "Verification:"
    python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
fi

vader_header "PyTorch Installation Complete"
