#!/bin/bash
# Build or refresh the index for one checkout (default: this Guaardvark root).
# Re-running is incremental. The index lives in <root>/.zvec-grep, which the
# repository ignores.
set -e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
ROOT="${1:-$PROJECT_ROOT}"
[ -x "$ZG" ] || { echo "zvec-grep is not installed; run scripts/setup.sh (the plugin's Install) first"; exit 1; }
# zg honours .gitignore but not .git/info/exclude, and a checkout's runtime
# state (data/, logs/, private workspace notes) is not source. Feed it the
# exclude file when there is one and keep the state directories out by name,
# so the index holds what the repository holds.
EXTRA=()
[ -f "$ROOT/.git/info/exclude" ] && EXTRA+=(--ignore-file "$ROOT/.git/info/exclude")
for skip in data logs pids docs/local-workspace-only local-workspace-only; do
    EXTRA+=(-g "!$skip/**")
done
exec "$ZG" index "$ROOT" --embedding "$ZVEC_GREP_EMBEDDING" --device "$ZVEC_GREP_DEVICE" --mode direct "${EXTRA[@]}" "${@:2}"
