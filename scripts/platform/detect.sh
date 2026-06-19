#!/usr/bin/env bash
# scripts/platform/detect.sh — platform detection for the start.sh launcher.
#
# STATUS: PROPOSAL — nothing sources this yet. Part of the "detect → route to a
# platform backend" refactor. Pure detection, no side effects.
#
# When wired, start.sh will `source` this near the top, call detect_platform(),
# then `source "$GUAARDVARK_PLATFORM_BACKEND"`. Sets:
#   GUAARDVARK_OS                linux | macos | unknown
#   GUAARDVARK_ARCH              x86_64 | arm64 | <raw>
#   GUAARDVARK_ACCEL             cuda | rocm | mps | cpu   (coarse; hardware_policy.py refines)
#   GUAARDVARK_IS_WSL            0 | 1
#   GUAARDVARK_PLATFORM_BACKEND  path to the backend .sh to source
#
# Design notes:
#   - Raspberry Pi is NOT a separate backend — it's linux.sh with GUAARDVARK_ARCH=arm64.
#   - WSL is NOT a separate backend — it's linux.sh with GUAARDVARK_IS_WSL=1.
#   - Only two backend files exist (linux.sh, macos.sh); the variations above are flags.

detect_platform() {
    local uname_s uname_m
    uname_s=$(uname -s 2>/dev/null || echo unknown)
    uname_m=$(uname -m 2>/dev/null || echo unknown)

    case "$uname_s" in
        Linux)  GUAARDVARK_OS=linux ;;
        Darwin) GUAARDVARK_OS=macos ;;
        *)      GUAARDVARK_OS=unknown ;;
    esac

    case "$uname_m" in
        x86_64|amd64)  GUAARDVARK_ARCH=x86_64 ;;
        aarch64|arm64) GUAARDVARK_ARCH=arm64 ;;
        *)             GUAARDVARK_ARCH=$uname_m ;;
    esac

    # WSL leaves a Microsoft/WSL marker in /proc/version (WSL1 + WSL2). Linux only.
    GUAARDVARK_IS_WSL=0
    if [ "$GUAARDVARK_OS" = linux ] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        GUAARDVARK_IS_WSL=1
    fi

    # Coarse accelerator detect — for ROUTING only. The runtime trusts
    # hardware_policy.platform_profile() (single source of truth). Order: CUDA > ROCm > MPS > CPU.
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        GUAARDVARK_ACCEL=cuda
    elif command -v rocminfo >/dev/null 2>&1; then
        GUAARDVARK_ACCEL=rocm
    elif [ "$GUAARDVARK_OS" = macos ] && [ "$GUAARDVARK_ARCH" = arm64 ]; then
        GUAARDVARK_ACCEL=mps
    else
        GUAARDVARK_ACCEL=cpu
    fi

    case "$GUAARDVARK_OS" in
        macos) GUAARDVARK_PLATFORM_BACKEND="$SCRIPT_DIR/scripts/platform/macos.sh" ;;
        *)     GUAARDVARK_PLATFORM_BACKEND="$SCRIPT_DIR/scripts/platform/linux.sh" ;;
    esac

    export GUAARDVARK_OS GUAARDVARK_ARCH GUAARDVARK_ACCEL GUAARDVARK_IS_WSL GUAARDVARK_PLATFORM_BACKEND
}
