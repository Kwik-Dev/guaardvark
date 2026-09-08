#!/bin/bash
# Start the shared zg daemon on loopback with the two-tool agent surface.
# The MCP client reaches it through `zg server --stdio` (see
# mcp_servers.example.json); this daemon is what that bootstrap reuses.
set -e
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
[ -x "$ZG" ] || { echo "zvec-grep is not installed; run scripts/setup.sh (the plugin's Install) first"; exit 1; }
if "$ZG" server status --check-ready >/dev/null 2>&1; then
    echo "zvec-grep server already running on $LISTEN"
    exit 0
fi
"$ZG" server on --listen "$LISTEN" --mcp-toolset agent
for i in $(seq 1 20); do
    if "$ZG" server status --check-ready >/dev/null 2>&1; then
        echo "zvec-grep server ready on $LISTEN"
        exit 0
    fi
    sleep 1
done
echo "zvec-grep server did not report ready within 20s"
exit 1
