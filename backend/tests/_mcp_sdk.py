"""Make ``mcp`` mean the installed MCP SDK in a backend test session.

``backend/tests/conftest.py`` puts ``backend/`` first on ``sys.path``. That
makes ``backend/mcp`` (Guaardvark's own MCP server package) importable as a
top-level ``mcp``, ahead of the SDK in site-packages, and it has no ``types``
module. ``backend/mcp/tools_adapter.py`` imports ``mcp.types``, so whichever
test module is collected first and imports the adapter binds ``mcp`` to the
wrong package and fails with "No module named 'mcp.types'".

A test module that imports ``backend.mcp.tools_adapter`` (or the SDK itself)
at module level calls ``use_mcp_sdk()`` before that import. It must not rely
on another module having loaded the SDK earlier: collection order changes
whenever a file is added to the run.
"""

from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def use_mcp_sdk():
    """Put the installed SDK in ``sys.modules["mcp"]`` and return it.

    A ``backend/mcp`` already loaded under the name ``mcp`` is dropped first.
    ``backend.mcp`` itself is a different entry and is left alone. An SDK that
    is not installed is an ImportError here, not a skip.
    """
    loaded = sys.modules.get("mcp")
    if loaded is not None and (getattr(loaded, "__file__", "") or "").startswith(_BACKEND_DIR + os.sep):
        for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
            del sys.modules[name]
    saved = list(sys.path)
    try:
        sys.path = [p for p in sys.path if os.path.abspath(p or ".") != _BACKEND_DIR]
        import mcp.types  # noqa: F401
    finally:
        sys.path = saved
    return sys.modules["mcp"]
