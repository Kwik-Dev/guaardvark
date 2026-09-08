#!/usr/bin/env bash
# scripts/platform/strip_appledouble.sh — remove macOS AppleDouble "._*" sidecars
# from a checkout. Prints the number of files removed. No-op off Darwin.
#
# macOS writes a "._name" sidecar next to any file that carries extended
# attributes when the volume cannot store them natively (exFAT, SMB, many
# external drives). The sidecar is a small binary file with the original's
# name, so "._pipeline.py" ends in .py and every loader that globs a directory
# trips over it:
#   - transformers reads each *.py under transformers/models at import time, so
#     `import diffusers` dies with "UnicodeDecodeError: 'utf-8' codec can't
#     decode byte 0xb0 in position 37" (byte 37 of the AppleDouble header).
#   - blueprint discovery logs "No module named 'backend.api.'" for every
#     "._foo_api.py" it finds in backend/api.
# Both were seen on the #41 tester's external-volume install. The files are
# metadata only (Finder tags, quarantine flags); nothing in the product needs
# them, and macOS recreates them as required.
#
# Usage: strip_appledouble.sh <checkout-root>
set -u

root="${1:-}"
if [ -z "$root" ] || [ ! -d "$root" ]; then
    echo "usage: $0 <checkout-root>" >&2
    exit 2
fi

if [ "$(uname -s 2>/dev/null)" != "Darwin" ]; then
    echo 0
    exit 0
fi

# .git is pruned for speed only; git never creates these files. node_modules
# is scanned: the frontend and zvec_grep trees are import-scanned too.
count=$(find "$root" -path "$root/.git" -prune -o -type f -name '._*' -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "${count:-0}"
