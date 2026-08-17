#!/usr/bin/env bash
#
# Refuse machine-specific content in tracked files.
#
# This repo is public and is forked into customer projects, so nothing may be
# tied to one machine, one home directory or one person. Paths come from the
# repo root or the environment; comments are written for future contributors,
# never for whoever happens to be running it today.
#
# Usage: scripts/check_portable.sh   (exits non-zero on a finding)
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Paths allowed to contain these patterns, as extended regex matched against
# the repo-relative path. Tests need fake home directories; the architecture
# doc and funding links legitimately name things.
ALLOW='^(backend/tests/|cli/tests/|frontend/src/.*\.test\.(js|jsx)$|docs/ARCHITECTURE\.md$|\.github/FUNDING\.yml$|README\.md$|scripts/check_portable\.sh$)'

# pattern<TAB>human explanation
#
# Only literal absolute home paths are flagged. A "~/..." string passed through
# expanduser() resolves per-user and is the correct portable pattern, so it is
# deliberately not matched here.
RULES=$(cat <<'PATTERNS'
/home/[a-z]	an absolute home directory — derive from the repo root or read an env var
\.ts\.net	a private tailnet hostname
100\.(6[4-9]|[7-9][0-9]|1[0-1][0-9]|12[0-7])\.[0-9]+\.[0-9]+	a tailnet IP address
PATTERNS
)

status=0
while IFS=$'\t' read -r pattern explanation; do
    [ -z "$pattern" ] && continue
    while IFS= read -r -d '' file; do
        [[ "$file" =~ $ALLOW ]] && continue
        if hits=$(grep -nE -- "$pattern" "$file" 2>/dev/null); then
            while IFS= read -r hit; do
                echo "✗ ${file}:${hit%%:*} — ${explanation}"
                status=1
            done <<< "$hits"
        fi
    done < <(git ls-files -z)
done <<< "$RULES"

if [ "$status" -ne 0 ]; then
    echo
    echo "Machine-specific content found in tracked files."
    echo "Derive paths from the repo root (Path(__file__).resolve().parents[N])"
    echo "or read them from an environment variable with a portable default."
    exit 1
fi

echo "✓ No machine-specific paths or hosts in tracked files."
