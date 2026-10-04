#!/bin/bash
# plugins/audio_foundry/scripts/setup_music15.sh
# Build plugins/audio_foundry/venv-music15/, the Python environment ACE-Step 1.5 runs in.
#
# Run by Install in Audio Studio -> Manage models (backend/services/audio_foundry_models.py),
# never at plugin start. It fetches the ACE-Step 1.5 source at the commit pinned in
# backends/acestep15_files.json and installs the exact package versions that release's
# uv.lock records (from PyPI and download.pytorch.org) with uv, itself pinned. The weights
# are a separate step of the same Install. Linux x86_64 with an NVIDIA GPU only.
#
# Usage: setup_music15.sh
# Env:   PYTHON_CMD       interpreter to build with (default python3.12, the lock's Python)
#        ACESTEP15_SOURCE a mirror or local clone of the ACE-Step 1.5 repo (the pinned
#                         commit is still checked)
#        UV_BIN           an existing uv of the pinned version, instead of installing one
# Exit 0 when the environment is built and checked; non-zero with a FAILED line otherwise.
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
# Both paths feed rm -rf below; refuse to run if either failed to resolve.
[ -n "$PLUGIN_DIR" ] && [ -d "$PLUGIN_DIR/backends" ] || { echo "FATAL: PLUGIN_DIR unresolved" >&2; exit 1; }
[ -n "$REPO_ROOT" ] && [ -d "$REPO_ROOT/backend" ] || { echo "FATAL: REPO_ROOT unresolved" >&2; exit 1; }
CATALOG="$PLUGIN_DIR/backends/acestep15_files.json"

log() { echo "  [audio_foundry/setup_music15] $*"; }
die() { log "FAILED: $*"; exit 1; }

[ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ] \
    || die "ACE-Step 1.5 here needs Linux x86_64 with an NVIDIA GPU (this machine is $(uname -s) $(uname -m))"
command -v nvidia-smi >/dev/null 2>&1 || die "no NVIDIA driver found (nvidia-smi); ACE-Step 1.5 here needs an NVIDIA GPU"
command -v git >/dev/null 2>&1 || die "git not found"

PY="${PYTHON_CMD:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || die "$PY not found (install python3.12 and python3.12-venv, or set PYTHON_CMD)"
PY="$(command -v "$PY")"

# pin <dotted.key>: one value from the catalog.
pin() {
    "$PY" - "$CATALOG" "$1" <<'PYEOF'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in sys.argv[2].split("."):
    value = value[key]
print(value)
PYEOF
}

WANT_PY="$(pin source.python)"
HAVE_PY="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[ "$HAVE_PY" = "$WANT_PY" ] || die "$PY is Python $HAVE_PY; the release's lock is resolved for Python $WANT_PY (set PYTHON_CMD)"

SRC_URL="${ACESTEP15_SOURCE:-$(pin source.repo)}"
TAG="$(pin source.tag)"
COMMIT="$(pin source.commit)"
UV_VERSION="$(pin source.uv)"
VENV="$PLUGIN_DIR/$(pin environment)"
MARKER="$VENV/.acestep15-source"
BUILD="$REPO_ROOT/data/cache/acestep15-build"

cleanup() { rm -rf "$BUILD"; }
trap cleanup EXIT
rm -rf "$BUILD"
mkdir -p "$BUILD"

log "Fetching ACE-Step 1.5 $TAG from $SRC_URL"
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$TAG" "$SRC_URL" "$BUILD/src" \
    || die "could not fetch $TAG from $SRC_URL"
GOT="$(git -C "$BUILD/src" rev-parse HEAD)"
[ "$GOT" = "$COMMIT" ] || die "$TAG is at $GOT, not the pinned $COMMIT; not building from it"

if [ -n "${UV_BIN:-}" ]; then
    UV="$UV_BIN"
else
    log "Installing uv $UV_VERSION into a throwaway build venv"
    "$PY" -m venv "$BUILD/tools" || die "could not create a venv with $PY (python3.12-venv missing?)"
    "$BUILD/tools/bin/pip" install --quiet --disable-pip-version-check "uv==$UV_VERSION" \
        || die "pip could not install uv $UV_VERSION"
    UV="$BUILD/tools/bin/uv"
fi
UV_HAVE="$("$UV" --version 2>/dev/null | awk '{print $2}' || true)"
[ "$UV_HAVE" = "$UV_VERSION" ] || die "uv is $UV_HAVE, not the pinned $UV_VERSION"

log "Building $VENV from the release's uv.lock (torch and CUDA libraries: several GB)"
rm -rf "$VENV"
# The cache lives in the build folder unless the caller names one, so the wheels do not
# stay behind in ~/.cache/uv once they are installed.
UV_PROJECT_ENVIRONMENT="$VENV" UV_PYTHON_DOWNLOADS=never \
UV_CACHE_DIR="${UV_CACHE_DIR:-$BUILD/uv-cache}" \
    "$UV" sync --project "$BUILD/src" --frozen --no-dev --no-editable --python "$PY" \
    || die "uv sync of the ACE-Step 1.5 lock failed"

log "Checking the environment"
(cd "$BUILD" && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 GRADIO_ANALYTICS_ENABLED=False \
    "$VENV/bin/python" - <<'PYEOF') || die "the built environment cannot import ACE-Step 1.5 or see the GPU"
import torch
import acestep.handler, acestep.inference, acestep.llm_inference  # noqa: F401
if not torch.cuda.is_available():
    raise SystemExit("torch %s reports no usable CUDA device" % torch.__version__)
major, minor = torch.cuda.get_device_capability(0)
print("torch", torch.__version__, "on", torch.cuda.get_device_name(0),
      "sm_%d%d" % (major, minor), "built for", " ".join(torch.cuda.get_arch_list()))
PYEOF

echo "$COMMIT" > "$MARKER"
log "Done: $VENV ($(du -sh "$VENV" | cut -f1))"
