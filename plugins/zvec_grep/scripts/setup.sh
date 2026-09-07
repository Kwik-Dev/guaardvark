#!/bin/bash
# The plugin's Install step. This is the only script that downloads anything:
# the pinned npm package (package-lock.json) and the local embedding model,
# fetched once into cache/models. Nothing here runs at startup.
set -e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
if ! command -v node >/dev/null 2>&1 || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 22 ]; then
    echo "zvec-grep needs Node.js 22 or newer (found: $(node -v 2>/dev/null || echo none))"
    exit 1
fi
cd "$PLUGIN_ROOT"
echo "Installing @zvec/zvec-grep (pinned by package-lock.json)..."
npm ci --no-audit --no-fund
echo "Fetching the local embedding model $ZVEC_GREP_EMBEDDING into $ZVEC_GREP_MODEL_CACHE ..."
SEED="$(mktemp -d)"; trap 'rm -rf "$SEED"' EXIT
echo "def seed(): return 1" > "$SEED/seed.py"
"$ZG" index "$SEED" --embedding "$ZVEC_GREP_EMBEDDING" --device "$ZVEC_GREP_DEVICE" --mode direct >/dev/null
"$ZG" index "$SEED" --drop --yes --mode direct >/dev/null 2>&1 || true
echo "zvec-grep $("$ZG" version) ready. Index a checkout with scripts/index.sh <path>."
