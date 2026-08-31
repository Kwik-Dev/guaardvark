#!/bin/bash
# plugins/video_editor/scripts/setup_venv.sh
# Provision the video_editor isolated venv against the hardware policy.
set -u

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
# Guard the path we `rm -rf` against: an empty $venv (from a failed cd) would
# make `rm -rf ""` resolve to the CWD. Refuse rather than risk it.
[ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR" ] || { echo "FATAL: PLUGIN_DIR unresolved" >&2; exit 1; }
[ -n "$REPO_ROOT" ]  && [ -d "$REPO_ROOT"  ] || { echo "FATAL: REPO_ROOT unresolved"  >&2; exit 1; }
PY="${PYTHON_CMD:-python3.12}"
INSTALL_PYTORCH="$REPO_ROOT/scripts/install_pytorch.sh"
BACKEND_PY="$REPO_ROOT/backend/venv/bin/python"
TORCH_CHANNEL="$("$BACKEND_PY" -m backend.services.hardware_policy torch_channel 2>/dev/null || echo "")"

log() { echo "  [video_editor/setup_venv] $*"; }

verify_venv() {
    local venv="$1"
    [ -x "$venv/bin/python" ] || return 1
    "$venv/bin/python" - <<'PYEOF'
import sys
try:
    import torch
    if torch.cuda.is_available():
        torch.zeros(1).cuda()
    print("video_editor torch OK", torch.__version__)
except Exception as e:
    print(f"VERIFY_FAIL: {e!r}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

venv="$PLUGIN_DIR/venv"
if verify_venv "$venv"; then
    log "venv healthy — skipping"
    exit 0
fi

log "Building venv at $venv"
rm -rf "$venv"
"$PY" -m venv "$venv" || { log "venv create failed"; exit 1; }
"$venv/bin/pip" install --upgrade pip >/dev/null 2>&1
for req in "$PLUGIN_DIR"/requirements*.txt; do
    [ -f "$req" ] && "$venv/bin/pip" install -r "$req"
done
TARGET_VENV="$venv" GUAARDVARK_TORCH_CHANNEL="$TORCH_CHANNEL" bash "$INSTALL_PYTORCH" --venv "$venv" \
    || log "install_pytorch returned non-zero (continuing to verify)"

if verify_venv "$venv"; then
    log "venv rebuilt + verified"
    exit 0
fi
log "DEGRADED: video_editor venv failed to import torch on channel '$TORCH_CHANNEL'."
exit 1
