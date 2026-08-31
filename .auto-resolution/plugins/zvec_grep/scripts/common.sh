# Shared by the plugin scripts. Everything zg writes stays under the plugin:
# runtime state and the daemon in cache/, the downloaded embedding model in
# cache/models, and each indexed checkout's index in <root>/.zvec-grep.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
ZG="$PLUGIN_ROOT/node_modules/.bin/zg"
export ZVEC_GREP_HOME="$PLUGIN_ROOT/cache"
export ZVEC_GREP_MODEL_CACHE="$PLUGIN_ROOT/cache/models"
export ZVEC_GREP_DEVICE="${ZVEC_GREP_DEVICE:-cpu}"
export ZVEC_GREP_EMBEDDING="${ZVEC_GREP_EMBEDDING:-local/potion-code-16m-v2}"
# The remote embedding providers are never configured: no key, no endpoint,
# no --allow-remote. Unset here so a value in the user's shell cannot leak in.
unset ZVEC_GREP_API_KEY ZVEC_GREP_ENDPOINT DASHSCOPE_API_KEY QWEN_API_KEY
LISTEN="${ZVEC_GREP_LISTEN:-127.0.0.1:7999}"
mkdir -p "$ZVEC_GREP_HOME" "$ZVEC_GREP_MODEL_CACHE"
