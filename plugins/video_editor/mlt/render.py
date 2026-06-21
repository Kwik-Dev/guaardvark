"""Render a .mlt project to MP4 via the melt CLI.

Snap-confinement note: do NOT call /snap/shotcut/current/bin/melt directly —
that's the unwrapped binary that fails to load libmlt because LD_LIBRARY_PATH
isn't set. Use the top-level /snap/shotcut/current/melt wrapper script.

Cross-platform: resolution now prefers env, PATH, then Mac (Homebrew/Applications)
and Linux (snap/apt/flatpak) candidates. See service/config_loader.resolve_melt_path.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .proc import run_logged

logger = logging.getLogger(__name__)

# Import the shared resolver (added for Linux/macOS portability)
try:
    from ..service.config_loader import resolve_melt_path as _shared_resolve_melt
except Exception:
    _shared_resolve_melt = None  # fallback handled below


class MeltNotFound(RuntimeError):
    """Raised when the configured melt binary isn't on disk or executable."""


@dataclass
class RenderResult:
    output_path: Path
    duration_seconds: float
    returncode: int
    stderr_tail: str


def render_mlt(
    mlt_path: str | Path,
    output_path: str | Path,
    *,
    melt_path: str,
    vcodec: str = "libx264",
    acodec: str = "aac",
    extra_args: Optional[list[str]] = None,
    timeout_s: float = 600.0,
) -> RenderResult:
    """Invoke `melt project.mlt -consumer avformat:out.mp4 vcodec=... acodec=...`."""
    mlt = Path(mlt_path).resolve()
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not mlt.exists():
        raise FileNotFoundError(f"input .mlt not found: {mlt}")

    melt_bin = _resolve_melt(melt_path)

    cmd = [
        str(melt_bin),
        str(mlt),
        "-consumer",
        f"avformat:{out}",
        f"vcodec={vcodec}",
        f"acodec={acodec}",
    ]
    if extra_args:
        cmd.extend(extra_args)

    logger.info("render: %s", " ".join(cmd))
    proc = run_logged(cmd, timeout_s=timeout_s)

    if proc.returncode != 0 or not out.exists():
        tail = proc.output[-1500:]
        raise RuntimeError(f"melt render failed (rc={proc.returncode}): {tail}")

    duration = _probe_duration(out)
    return RenderResult(
        output_path=out,
        duration_seconds=duration,
        returncode=proc.returncode,
        stderr_tail=proc.output[-500:],
    )


def _resolve_melt(configured: str) -> Path:
    """Find an executable melt — cross-platform with shared resolver.

    Delegates to the improved resolve_melt_path when available (env + platform candidates).
    Falls back to legacy PATH + snap handling for compatibility.
    """
    # Prefer the shared cross-platform resolver (Linux/macOS candidates + env)
    if _shared_resolve_melt is not None:
        try:
            found = _shared_resolve_melt(configured)
            if found:
                return found
        except Exception:
            pass  # fall through to legacy

    p = Path(configured)
    if p.is_file():
        # Resolve snap "current" symlink so we don't break mid-render on a snap refresh.
        return p.resolve()

    found = shutil.which(configured) or shutil.which("melt")
    if found:
        return Path(found).resolve()

    raise MeltNotFound(
        f"melt binary not found (tried '{configured}' and $PATH). "
        "macOS: `brew install --cask shotcut` or `brew install mlt`. "
        "Linux: `apt install melt` / `flatpak install ...shotcut` / snap. "
        "Override with VIDEO_EDITOR_MELT_PATH env or melt.path in config.yaml."
    )


def _probe_duration(mp4: Path) -> float:
    """Best-effort duration probe via ffprobe; returns 0.0 if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp4),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return float(out.stdout.strip() or "0")
    except (subprocess.SubprocessError, ValueError):
        return 0.0
