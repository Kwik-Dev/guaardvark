"""Should start.sh ask the backend to start the ComfyUI plugin after boot?

The backend restores the plugins it recorded as running when it comes up
(PluginManager._init_plugin_status). start.sh runs this after the backend is
healthy as the second line: when the restore did not happen (the boot health
wait timed out, a tripped breaker skipped it) the first video generation
otherwise fails with "Start the ComfyUI plugin".

Standard library only: start.sh loads this file by path, the way it loads
comfyui_launch_flags.py, so no backend package import happens in a shell
step. The answer is a decision plus the reason, never an action; the caller
POSTs /api/plugins/comfyui/start, the same path the Plugins page toggle uses.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Tuple

PLUGIN_ID = "comfyui"
DEFAULT_PORT = 8188
# Same file types the ComfyUI manifest's instance_check counts as models.
MODEL_EXTENSIONS = (".safetensors", ".sft", ".gguf", ".ckpt", ".pt", ".pt2", ".pth", ".bin", ".pkl")
MODEL_SUBDIRS = ("ComfyUI/models/unet", "ComfyUI/models/diffusion_models", "ComfyUI/models/checkpoints")


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _manifest(root: Path) -> dict:
    """plugin.json with the untracked plugin.local.json overlay (config merged)."""
    plugin_dir = root / "plugins" / PLUGIN_ID
    data = _load_json(plugin_dir / "plugin.json")
    local = _load_json(plugin_dir / "plugin.local.json")
    if local:
        local_config = local.pop("config", {}) or {}
        data.update(local)
        if local_config:
            data.setdefault("config", {}).update(local_config)
    return data


def _truthy(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def effective_enabled(root: Path, state: dict, manifest: dict, environ=None) -> bool:
    """The user's toggle wins, then the active profile's default, then the manifest."""
    prefs = state.get("user_enabled") or {}
    if PLUGIN_ID in prefs:
        return bool(prefs[PLUGIN_ID])
    env = environ if environ is not None else os.environ
    for item in (env.get("GUAARDVARK_PROFILE_PLUGIN_DEFAULTS") or "").split(","):
        if "=" in item and item.split("=", 1)[0].strip() == PLUGIN_ID:
            return _truthy(item.split("=", 1)[1])
    config = manifest.get("config") or {}
    return _truthy(config.get("default_enabled", config.get("enabled", False)))


def has_video_models(root: Path) -> bool:
    plugin_dir = root / "plugins" / PLUGIN_ID
    manifest = _manifest(root)
    spec = manifest.get("instance_check") or {}
    subdirs = spec.get("lists_file_from") or list(MODEL_SUBDIRS)
    extensions = tuple(e.lower() for e in (spec.get("extensions") or MODEL_EXTENSIONS))
    for sub in subdirs:
        base = plugin_dir / sub
        if not base.is_dir():
            continue
        for _dirpath, _dirs, files in os.walk(base):
            if any(name.lower().endswith(extensions) for name in files):
                return True
    return False


def port_answers(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def should_start_comfyui(root, *, environ=None, probe=port_answers) -> Tuple[bool, str]:
    """(start?, reason) for this checkout.

    Starts only when the plugin is effectively enabled, it was running before
    the last stop (data/plugin_state.json ``running``, which the backend keeps
    and stop.sh does not clear) or the manifest sets default_auto_start, video
    models are installed, and nothing already answers on its port.
    """
    root = Path(root)
    state = _load_json(root / "data" / "plugin_state.json")
    manifest = _manifest(root)
    if not manifest:
        return False, "no ComfyUI plugin manifest"
    if not effective_enabled(root, state, manifest, environ):
        return False, "the ComfyUI plugin is disabled"
    was_running = PLUGIN_ID in (state.get("running") or [])
    config = manifest.get("config") or {}
    auto_start = _truthy(config.get("default_auto_start", config.get("auto_start", False)))
    if not (was_running or auto_start):
        return False, "ComfyUI was not running before the last stop and is not set to auto-start"
    if not has_video_models(root):
        return False, "no video models are installed yet (nothing for ComfyUI to serve)"
    try:
        port = int(manifest.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if probe(port):
        return False, f"something already answers on port {port}"
    why = "it was running before the last stop" if was_running else "the manifest sets default_auto_start"
    return True, f"{why} and video models are installed"


def main(argv=None) -> int:
    """Print ``yes|reason`` or ``no|reason`` for the checkout given as argv[1]."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = args[0] if args else os.getcwd()
    start, reason = should_start_comfyui(root)
    print(f"{'yes' if start else 'no'}|{reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
