#!/bin/bash
set -e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[ -x "$ZG" ] || exit 0
"$ZG" server off 2>/dev/null || true
