#!/bin/bash
# Restore the ComfyUI *application* tree without wiping machine-local weights.
#
# Why this exists:
#   - plugins/comfyui/ComfyUI/models/ can be 100GB+ of checkpoints (machine-local).
#   - The app sources (main.py, comfy/, custom_nodes deps, etc.) can go missing or
#     become incomplete after a bad sync.
#   - A naive `rsync --exclude 'models'` also deletes nested Python packages like
#     comfy/ldm/models (ModuleNotFoundError: comfy.ldm.models).
#
# Rule: exclude ONLY the top-level weights directory: --exclude '/models/'
#
# Usage:
#   bash plugins/comfyui/scripts/restore_app.sh
#   bash plugins/comfyui/scripts/restore_app.sh --dry-run
#   COMFYUI_REPO=https://github.com/comfyanonymous/ComfyUI bash .../restore_app.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMFYUI_DIR="$PLUGIN_ROOT/ComfyUI"
REPO_URL="${COMFYUI_REPO:-https://github.com/comfyanonymous/ComfyUI}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
  esac
done

err() { echo "Error: $*" >&2; }
info() { echo "$*"; }

if ! command -v git >/dev/null 2>&1; then
  err "git is required"
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  err "rsync is required"
  exit 1
fi

TMP_DIR="$(mktemp -d -t comfyui-restore-XXXXXX)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

info "Cloning ComfyUI app sources into temp dir..."
info "  repo: $REPO_URL"
git clone --depth 1 "$REPO_URL" "$TMP_DIR/ComfyUI"

mkdir -p "$COMFYUI_DIR"

# Preserve top-level weights, custom nodes, and per-machine user state.
# Nested package paths like comfy/ldm/models MUST still update from the fresh clone.
# NEVER delete custom_nodes/ — Wan depends on KJNodes/GGUF/VHS/CogVideoXWrapper.
RSYNC_OPTS=(-a --delete --exclude '/models/' --exclude '/custom_nodes/' --exclude '/user/' --exclude '/input/' --exclude '/output/' --exclude '/temp/')
if [ "$DRY_RUN" -eq 1 ]; then
  RSYNC_OPTS+=(--dry-run -v)
  info "Dry-run rsync (no files written):"
else
  info "Merging app into $COMFYUI_DIR (preserving models/, custom_nodes/, user/)..."
fi

# Pin to a release tag. LTX-2.5 needs ≥ 0.32.0 (LTXVDualCFGGuider + DiffVAE).
# Override with COMFYUI_REF=v0.31.1 (or empty string for unpinned HEAD) only
# if you are deliberately staying off 2.5.
#   COMFYUI_REF=v0.32.0 bash plugins/comfyui/scripts/restore_app.sh
COMFYUI_REF="${COMFYUI_REF-v0.32.0}"
if [ -n "$COMFYUI_REF" ]; then
  info "Checking out COMFYUI_REF=$COMFYUI_REF in temp clone..."
  git -C "$TMP_DIR/ComfyUI" fetch --tags --depth 1 origin "refs/tags/$COMFYUI_REF:refs/tags/$COMFYUI_REF" 2>/dev/null \
    || git -C "$TMP_DIR/ComfyUI" fetch --tags origin "$COMFYUI_REF"
  git -C "$TMP_DIR/ComfyUI" checkout -q "$COMFYUI_REF"
fi

rsync "${RSYNC_OPTS[@]}" "$TMP_DIR/ComfyUI/" "$COMFYUI_DIR/"

if [ ! -f "$COMFYUI_DIR/main.py" ]; then
  err "Restore failed: $COMFYUI_DIR/main.py still missing"
  exit 1
fi
if [ ! -f "$COMFYUI_DIR/comfy/ldm/models/autoencoder.py" ]; then
  err "Restore failed: nested comfy/ldm/models/autoencoder.py missing"
  exit 1
fi

info "ComfyUI app tree OK."
info "  main.py: $COMFYUI_DIR/main.py"
info "  nested models package: present"
info "  top-level weights: preserved at $COMFYUI_DIR/models/ (if any)"
info "Next:"
info "  bash $SCRIPT_DIR/install_deps.sh"
info "  bash $SCRIPT_DIR/start.sh"
info "  or toggle the ComfyUI plugin in the UI after backend restart"
