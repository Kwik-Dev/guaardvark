#!/usr/bin/env bash
# scripts/platform/linux.sh — Linux platform backend (x86_64, aarch64/Pi, WSL).
#
# STATUS: PROPOSAL — not sourced by anything yet.
#
# Covers Ubuntu/Debian/Raspberry Pi OS via apt + systemctl.
#   - Raspberry Pi  = this file with GUAARDVARK_ARCH=arm64.
#   - WSL           = this file with GUAARDVARK_IS_WSL=1 (systemd may be absent → guarded).
#
# INCREMENTAL ROLLOUT NOTE: the existing inline Linux logic in start.sh keeps running
# as-is during adoption. This backend is the eventual extraction target so all three
# platforms share ONE interface. Wiring macos.sh first (the new code) is zero-risk to
# the working Linux/CUDA boot; lifting Linux into here is a later, optional symmetry pass.
# Assumes the vader_* log helpers from start.sh are in scope.

platform_install_system_deps() {
    vader_info "Installing system deps via apt (postgresql, redis, ffmpeg, node, build tools, zstd)..."
    sudo apt-get install -y postgresql postgresql-contrib redis-server ffmpeg nodejs npm cmake build-essential zstd || return 1
}

platform_ensure_python() {
    if command -v python3.12 >/dev/null 2>&1; then PYTHON_CMD=python3.12; export PYTHON_CMD; return 0; fi
    if [ "$GUAARDVARK_ARCH" = arm64 ]; then
        # Raspberry Pi OS (Debian 13/trixie) ships 3.13 and has no apt python3.12.
        vader_error "Python 3.12 required. On a Pi (no apt python3.12): 'uv python install 3.12' (or pyenv), then 'PYTHON_CMD=\$(uv python find 3.12) ./start.sh'."
    else
        vader_error "Python 3.12 required: 'sudo apt-get install -y python3.12 python3.12-venv python3.12-dev' (Ubuntu 22.04: add the deadsnakes PPA first), then 'PYTHON_CMD=python3.12 ./start.sh'."
    fi
    return 1
}

platform_gpu_setup() {
    if [ "$GUAARDVARK_ACCEL" = cuda ]; then
        # The existing nvidia persistence/power-limit + ollama systemd drop-in logic
        # lives here when extracted. Guarded by nvidia-smi/systemctl presence today.
        vader_info "Linux + NVIDIA: GPU tuning (persistence/power, ollama systemd drop-in)."
    else
        vader_info "Linux ($GUAARDVARK_ARCH, accel=$GUAARDVARK_ACCEL): no NVIDIA tuning."
    fi
}

platform_service_start() {  # $1 = postgres | redis | ollama  (systemctl)
    # WSL frequently has no systemd → don't hard-fail; log and move on.
    if [ "$GUAARDVARK_IS_WSL" = 1 ] && ! systemctl is-system-running >/dev/null 2>&1; then
        vader_info "WSL without systemd: start '$1' via its init script (e.g. 'sudo service $1 start') or manually."
        return 0
    fi
    sudo systemctl start "$1" 2>/dev/null || vader_warn "Could not start '$1' via systemctl — start it manually."
}
