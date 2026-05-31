"""
Test bootstrap for the swarm plugin.

The swarm sidecar runs with PYTHONPATH=<plugin_root> and imports its code as
`service.<module>` (see scripts/start.sh). Mirror that here so `import service.app`
and friends resolve the same way they do in production.
"""

import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
