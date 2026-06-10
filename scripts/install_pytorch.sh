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
VENV_PIP="$PROJECT_ROOT/backend/venv/bin/pip"
VENV_PYTHON="$PROJECT_ROOT/backend/venv/bin/python"

if [ -x "$VENV_PIP" ] && [ -x "$VENV_PYTHON" ]; then
    # Project venv exists — prefer its pip unconditionally so we never touch
    # system Python regardless of what's on PATH.
    vader_info "Using project venv: $PROJECT_ROOT/backend/venv"
    pip() { "$VENV_PIP" "$@"; }
    # Also route `python3` calls in the verification blocks through the venv.
    python3() { "$VENV_PYTHON" "$@"; }
else
    # No project venv found — require that something is activated, otherwise
    # refuse to run rather than wreck system Python.
    if [ -z "${VIRTUAL_ENV:-}" ]; then
        vader_warn "No project venv at $PROJECT_ROOT/backend/venv AND no active virtualenv."
        vader_warn "Refusing to install torch into system Python. Activate a venv first, or"
        vader_warn "create the project venv with: python3 -m venv $PROJECT_ROOT/backend/venv"
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

        # Determine which CUDA version to use with detailed explanation
        vader_section "Architecture Detection:"

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

        vader_section "Installation Plan:"

        # --force-reinstall is required because pip's resolver treats the
        # local-version tag (e.g. +cu130 vs +cpu) as the SAME version number
        # for "already satisfied" purposes. Without --force-reinstall, a machine
        # restored from a GPU host's backup will report success but keep the
        # wrong variant. Also uninstall any lingering CUDA/triton deps that
        # were pulled in by a previous GPU build so we don't carry dead weight.
        vader_section "Cleaning prior torch variants and CUDA dependency bloat..."
        pip uninstall -y torch torchvision torchaudio 2>/dev/null | tail -3 || true
        pip freeze 2>/dev/null | grep -iE "^(nvidia-|cuda-bindings|cuda-pathfinder|cuda-toolkit|triton)" | awk -F'==' '{print $1}' | xargs -r pip uninstall -y 2>/dev/null | tail -3 || true

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
        pip uninstall -y pynvml 2>/dev/null | tail -2 || true

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
    pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
    pip uninstall -y pynvml 2>/dev/null | tail -2 || true

    vader_section "Verification:"
    python3 -c "import torch; print(f'    PyTorch Version: {torch.__version__}'); print(f'    Mode: CPU-only')"
fi

vader_header "PyTorch Installation Complete"
