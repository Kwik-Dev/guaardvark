"""Load + merge plugin.json (manifest) and config.yaml (runtime overrides)."""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def project_root() -> Path:
    """Honor GUAARDVARK_ROOT (set by start.sh) — fall back to two-up from this file."""
    env = os.environ.get("GUAARDVARK_ROOT")
    if env:
        return Path(env).resolve()
    return _PLUGIN_ROOT.parent.parent


def load_config() -> dict[str, Any]:
    """Return the merged runtime config."""
    manifest = _read_json(_PLUGIN_ROOT / "plugin.json")
    runtime = _read_yaml(_PLUGIN_ROOT / "config.yaml")

    melt_path = _resolve_melt_path(runtime.get("melt", {}).get("path", ""))
    runtime.setdefault("melt", {})["resolved_path"] = str(melt_path) if melt_path else ""

    return {
        "manifest": manifest,
        "runtime": runtime,
        "paths": {
            "plugin_root": str(_PLUGIN_ROOT),
            "project_root": str(project_root()),
            "mlt_projects": str(_abs(runtime.get("output", {}).get("mlt_projects_dir", "data/outputs/videos/mlt-projects"))),
            "renders": str(_abs(runtime.get("output", {}).get("renders_dir", "data/outputs/videos/editor-renders"))),
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _abs(p: str) -> Path:
    """Resolve a project-relative path against the project root."""
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (project_root() / pp).resolve()


def resolve_melt_path(configured: str) -> Path | None:
    """Find an executable melt with cross-platform support (Linux/macOS).

    Order of precedence:
    1. VIDEO_EDITOR_MELT_PATH (or MELT_PATH) env var (set by start.sh or user)
    2. Explicit value from config.yaml
    3. shutil.which("melt")
    4. Common platform-specific locations (snap, Homebrew, /Applications, apt paths, etc.)

    Returns the first executable file found (resolved for symlinks) or None.
    """
    candidates: list[str] = []

    # 1. Env var (highest priority for overrides)
    for env_key in ("VIDEO_EDITOR_MELT_PATH", "MELT_PATH"):
        if val := os.environ.get(env_key):
            candidates.append(val)

    # 2. Configured path
    if configured:
        candidates.append(configured)

    # 3. PATH
    if found := shutil.which("melt"):
        candidates.append(found)

    # 4. Platform-specific common locations
    sysname = platform.system().lower()
    if sysname == "darwin":
        candidates.extend([
            "/Applications/Shotcut.app/Contents/MacOS/melt",
            "/Applications/Shotcut.app/Contents/Resources/melt",
            "/opt/homebrew/bin/melt",
            "/usr/local/bin/melt",
        ])
    elif sysname == "linux":
        candidates.extend([
            "/snap/shotcut/current/melt",
            "/var/lib/snapd/snap/shotcut/current/melt",
            "/usr/bin/melt",
            "/usr/local/bin/melt",
            "/opt/shotcut/melt",
            "/opt/shotcut/bin/melt",
        ])

    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return p.resolve()
        except Exception:
            continue

    return None


def _resolve_melt_path(configured: str) -> Path | None:
    """Backward-compatible wrapper (delegates to the public resolver)."""
    return resolve_melt_path(configured)
