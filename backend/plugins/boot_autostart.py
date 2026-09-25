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


def _extra_model_paths(config_path: Path) -> list:
    """``base_path`` values from a ComfyUI ``extra_model_paths.yaml``.

    Deliberately a base_path scan and not a YAML parse: all the guard needs is
    whether a tree was handed to the server, and the per-folder map *inside*
    that tree is ComfyUI's business. Missing or unreadable file -> no roots.
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []
    roots = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("base_path:"):
            value = stripped.split("base_path:", 1)[1].strip().strip("'\"")
            if value:
                roots.append(Path(value).expanduser())
    return roots


def _model_dirs(plugin_dir: Path, subdirs, environ) -> list:
    """Every directory the bundled ComfyUI server could serve models from.

    The plugin tree is the default home. A *shared* install keeps its weights
    outside the checkout and points the server at them, so the two documented
    routes are added as well:

      * ``GUAARDVARK_COMFYUI_DIR`` — the "shared model home" of
        ``docs/MACOS.md`` / ``docs/GUAARDVARK_GUIDE.md``. The weights then live
        in ``$GUAARDVARK_COMFYUI_DIR/models``, which is also the root the
        backend's model registry reads, so downloads land where ComfyUI reads.
      * every ``base_path`` in ``ComfyUI/extra_model_paths.yaml`` — the bridge
        the bundled server actually loads.

    Only the model folders ComfyUI needs to *serve* video are probed, never the
    whole tree: a stray ``.safetensors`` under ``loras/`` must not read as "a
    video model is installed". Standard library only, because start.sh loads
    this module by path and no backend import is available here.
    """
    leaves = [sub.split("models/", 1)[-1].strip("/") or "models" for sub in subdirs]
    dirs = [plugin_dir / sub for sub in subdirs]
    roots = []
    shared = (environ or {}).get("GUAARDVARK_COMFYUI_DIR", "").strip()
    if shared:
        roots.append(Path(shared).expanduser())
    roots.extend(_extra_model_paths(plugin_dir / "ComfyUI" / "extra_model_paths.yaml"))
    for base in roots:
        for leaf in leaves:
            # `<dir>/models/<leaf>` is the documented shared layout; `<dir>/<leaf>`
            # covers an operator who pointed the setting at the models dir itself.
            dirs.append(base / "models" / leaf)
            dirs.append(base / leaf)
    return dirs


def has_video_models(root: Path, environ=None) -> bool:
    """True when any reachable model tree holds a file ComfyUI can load.

    Scans the plugin tree plus the shared-model locations (``_model_dirs``): on
    a shared install the plugin's own tree is empty scaffolding and the weights
    live in the shared home, so probing the plugin tree alone answered "no video
    models are installed yet" forever and start.sh never auto-started ComfyUI.
    """
    env = os.environ if environ is None else environ
    plugin_dir = root / "plugins" / PLUGIN_ID
    manifest = _manifest(root)
    spec = manifest.get("instance_check") or {}
    subdirs = spec.get("lists_file_from") or list(MODEL_SUBDIRS)
    extensions = tuple(e.lower() for e in (spec.get("extensions") or MODEL_EXTENSIONS))
    for base in _model_dirs(plugin_dir, subdirs, env):
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
    env = os.environ if environ is None else environ
    state = _load_json(root / "data" / "plugin_state.json")
    manifest = _manifest(root)
    if not manifest:
        return False, "no ComfyUI plugin manifest"
    if not effective_enabled(root, state, manifest, env):
        return False, "the ComfyUI plugin is disabled"
    was_running = PLUGIN_ID in (state.get("running") or [])
    config = manifest.get("config") or {}
    auto_start = _truthy(config.get("default_auto_start", config.get("auto_start", False)))
    if not (was_running or auto_start):
        return False, "ComfyUI was not running before the last stop and is not set to auto-start"
    if not has_video_models(root, env):
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
