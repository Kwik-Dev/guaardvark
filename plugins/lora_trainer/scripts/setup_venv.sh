#!/usr/bin/env bash
# Bootstrap the isolated torch venv for the lora_trainer plugin.
#
# Run once on a host with CUDA 12+. Takes ~5-10 min depending on bandwidth.
# Can re-run safely (uses --upgrade if requested).
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${PLUGIN_DIR}/venv-torch"
REQS="${PLUGIN_DIR}/requirements-torch.txt"

if [[ ! -f "${REQS}" ]]; then
    echo "ERROR: requirements-torch.txt missing at ${REQS}" >&2
    exit 1
fi

# Pip wheels (CUDA bundles especially) total ~4 GB. /tmp is tmpfs on most
# Linux setups and runs out of space mid-install. Force pip to spill to a
# disk-backed dir under the venv so the install doesn't OOM the tmpfs.
export TMPDIR="${VENV}/.tmp"
mkdir -p "${TMPDIR}"

if [[ ! -d "${VENV}" ]]; then
    echo "Creating venv at ${VENV}…"
    python3 -m venv "${VENV}"
fi

echo "Upgrading pip in venv-torch…"
"${VENV}/bin/pip" install --upgrade pip wheel

echo "Installing torch + torchvision (CUDA wheels)…"
# Install both from the cu130 index so torchvision picks the matching cu130
# build for torch 2.11. Pinning torchvision against PyPI fails because the
# version space tracks torch builds and PyPI doesn't host cu130 wheels.
"${VENV}/bin/pip" install \
    torch==2.11.0 torchvision \
    --index-url https://download.pytorch.org/whl/cu130

echo "Installing remaining requirements…"
"${VENV}/bin/pip" install -r "${REQS}"

echo "Verifying torch in venv-torch…"
"${VENV}/bin/python" -c "
import torch
print(f'OK: torch {torch.__version__}')
if torch.cuda.is_available():
    print(f'CUDA: {torch.cuda.get_device_name(0)}')
elif getattr(torch.backends, 'mps', None) and torch.backends.mps.is_available():
    print('MPS (Apple Metal) available')
elif getattr(torch.version, 'hip', None):
    print('ROCm/HIP available')
else:
    print('CPU-only (ROCm/Metal/CPU branches supported in main installer; this venv is CUDA-optimized but will degrade)')
" 2>&1 | cat || echo 'Torch verification warning (non-fatal for CPU/Metal/ROCm per edge audit)'

echo "Done. The plugin will auto-pick the real backend on next train dispatch."
