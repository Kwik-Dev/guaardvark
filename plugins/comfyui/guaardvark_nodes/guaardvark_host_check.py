"""Guaardvark's Host check for the ComfyUI it launches. Defines no nodes.

ComfyUI has no authentication. Its own origin check compares a request's Host
with its Origin, and a page whose DNS name was re-pointed at 127.0.0.1 passes
it, since both name the page's site; that page could then queue workflows and
read /view, /history and /userdata. This puts the backend's Host rule
(backend/utils/host_check.py) in front of every route and websocket, first in
the middleware chain: a request addressed to a name that is not this
machine's gets 421. The ComfyUI page Guaardvark links to
(http://localhost:8188) and every backend client (GUAARDVARK_COMFYUI_URL,
localhost by default) are answered as before.

ComfyUI loads this file from plugins/comfyui/guaardvark_nodes/, which
guaardvark_model_paths.yaml adds to its custom node folders; both launchers
(scripts/start.sh and backend/services/video_generation_router.py) pass that
file. Nothing under plugins/comfyui/ComfyUI/ changes, so an update of ComfyUI
keeps it.

ComfyUI logs a custom node that fails to import and starts without it. So if
the rule cannot be loaded, every request is refused with 503 naming the cause
rather than served unchecked.
"""
import importlib.util
import logging
import sys
from pathlib import Path

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_GUARD_MODULE = "guaardvark_sidecar_guard"
logger = logging.getLogger(__name__)


def _load_guard():
    """backend/utils/sidecar_guard.py, loaded by path: ComfyUI runs outside
    the backend package."""
    loaded = sys.modules.get(_GUARD_MODULE)
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "sidecar_guard.py"
    spec = importlib.util.spec_from_file_location(_GUARD_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[_GUARD_MODULE] = module
    return module


def _refuse_every_request(reason: str):
    from aiohttp import web

    @web.middleware
    async def host_check_unavailable(request, handler):
        return web.json_response(
            {
                "error": f"Guaardvark's Host check for ComfyUI did not load ({reason}). See logs/comfyui.log.",
                "code": "host_check_unavailable",
            },
            status=503,
        )

    return host_check_unavailable


def host_check_middleware():
    try:
        return _load_guard().aiohttp_middleware()
    except Exception as exc:  # noqa: BLE001 - any failure must close the server, not open it
        logger.exception("Guaardvark Host check did not load; ComfyUI will refuse every request")
        return _refuse_every_request(f"{type(exc).__name__}: {exc}")


def install(app) -> None:
    """Put the Host check first in ``app``'s middleware chain. ComfyUI loads
    custom nodes after creating its server and before starting it, while the
    chain can still change."""
    app.middlewares.insert(0, host_check_middleware())


from server import PromptServer  # noqa: E402 - ComfyUI's server module, loaded before custom nodes

install(PromptServer.instance.app)
logger.info("Guaardvark Host check installed")
