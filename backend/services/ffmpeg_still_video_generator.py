"""Generate movie clips from still images using ffmpeg (no GPU, no AI model).

Unlike Image-to-Video, this NEVER re-renders the artwork. It moves the *camera*
only (or holds the frame still), so there is zero distortion and zero color shift
— ideal for picture-book thumbnails, infographics, or anything that must stay
pixel-perfect. Three motion patterns:

  - static           : a still frame held for the duration (pixel-perfect).
  - ken_burns_zoom   : slow push-in (zoom) over the duration.
  - ken_burns_pan    : slow left-to-right pan (camera dolly).

The generated clips are written under data/uploads/Videos/FFmpeg/<batch_id>/ and
registered as Documents so they appear in the Media Library / Files page.
"""

from __future__ import annotations

import logging
import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from backend.config import UPLOAD_DIR
from backend.services.output_registration import register_file

logger = logging.getLogger(__name__)

# The three camera-motion patterns surfaced in the UI.
PATTERNS = {
    "static": {
        "label": "Static (pixel-perfect)",
        "description": "Holds the image still for the duration. No movement at all.",
    },
    "ken_burns_zoom": {
        "label": "Ken Burns zoom",
        "description": "Slow camera push-in (zoom). The artwork is untouched.",
    },
    "ken_burns_pan": {
        "label": "Ken Burns pan",
        "description": "Slow camera pan. The artwork is untouched.",
    },
}

# Direction of travel for the ken_burns_pan pattern.
PAN_DIRECTIONS = {
    "left-to-right": {"label": "Pan left \u2192 right", "description": "Camera dollies left to right."},
    "right-to-left": {"label": "Pan right \u2192 left", "description": "Camera dollies right to left."},
    "top-to-bottom": {"label": "Pan top \u2192 bottom", "description": "Camera tilts top to bottom."},
    "bottom-to-top": {"label": "Pan bottom \u2192 top", "description": "Camera tilts bottom to top."},
}

# "random" is accepted by the drivers (API/CLI/UI) and resolved to a concrete
# direction per image inside generate_still_clip_batch. It is NOT in
# PAN_DIRECTIONS so the filter builder always gets a real travel direction.
PAN_CHOICES = [*PAN_DIRECTIONS.keys(), "random"]

# Output settings.
FFMPEG_DIR = Path(UPLOAD_DIR) / "Videos" / "FFmpeg"


def ffmpeg_available() -> bool:
    """True if the ffmpeg binary is on PATH."""
    return shutil.which("ffmpeg") is not None


def _resolve_input(path: str) -> Optional[Path]:
    """Resolve a user-supplied image path to an on-disk file."""
    p = Path(path)
    if p.is_absolute():
        return p if p.exists() else None
    # Relative to uploads, then cwd.
    cand = Path(UPLOAD_DIR) / path
    if cand.exists():
        return cand
    cwd = Path.cwd() / path
    return cwd if cwd.exists() else None


def _build_filter(pattern: str, width: int, height: int, duration_s: float, fps: int,
                  focus_x: float = 0.5, focus_y: float = 0.5,
                  pan_direction: str = "left-to-right") -> str:
    """Return the ffmpeg -vf filter graph for the given motion pattern.

    focus_x / focus_y (0.0–1.0) pick the point the camera keeps centered while
    zooming, and the fixed (non-moving) axis for a pan (default 0.5 = center).
    pan_direction selects the travel direction for the ken_burns_pan pattern.
    """
    fx = max(0.0, min(1.0, focus_x))
    fy = max(0.0, min(1.0, focus_y))
    frames = max(1, int(round(duration_s * fps)))

    if pattern == "static":
        # Scale to cover, center-crop, hold. `loop 1` input feeds one image in;
        # the -t handles the duration.
        return (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps}")

    if pattern == "ken_burns_zoom":
        # Slow steady zoom from 1.0 to _KB_ZOOM_MAX while keeping the focus point
        # centered in the crop. x/y are the crop top-left in the (scaled-up)
        # input; locking the crop center on (fx*iw, fy*ih) keeps the focus steady.
        max_zoom = 1.35
        step = (max_zoom - 1.0) / frames
        z = f"min({step:.6f}+zoom,{max_zoom:.4f})"
        x = f"{fx:.4f}*iw-iw/(2*zoom)"
        y = f"{fy:.4f}*ih-ih/(2*zoom)"
        return (f"scale=8000:-1,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps}")
    if pattern == "ken_burns_pan":
        # Pan at a fixed modest zoom. The travel axis moves across the frame; the
        # perpendicular axis stays fixed on the focus value.
        zoom = 1.20
        px = 3.0   # pixels/frame along x
        py = 3.0   # pixels/frame along y
        dirn = pan_direction if pan_direction in PAN_DIRECTIONS else "left-to-right"
        if dirn == "left-to-right":
            x = f"min({px}*on,iw-iw/zoom)"
            y = f"{fy:.4f}*ih-ih/(2*zoom)"
        elif dirn == "right-to-left":
            x = f"max(iw-iw/zoom-{px}*on,0)"
            y = f"{fy:.4f}*ih-ih/(2*zoom)"
        elif dirn == "top-to-bottom":
            x = f"{fx:.4f}*iw-iw/(2*zoom)"
            y = f"min({py}*on,ih-ih/zoom)"
        else:  # bottom-to-top
            x = f"{fx:.4f}*iw-iw/(2*zoom)"
            y = f"max(ih-ih/zoom-{py}*on,0)"
        return (f"scale=8000:-1,"
                f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps}")
    raise ValueError(f"Unknown ffmpeg pattern: {pattern}")


def generate_still_clip(
    *,
    image_path: str,
    output_path: str,
    pattern: str = "ken_burns_zoom",
    duration_s: float = 5.0,
    fps: int = 25,
    width: int = 1280,
    height: int = 720,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    pan_direction: str = "left-to-right",
) -> str:
    """Convert one still image to a video clip with the given camera pattern.

    focus_x / focus_y (0.0–1.0) select the point the camera keeps centered while
    zooming, and the fixed axis for a pan (default 0.5 = center). pan_direction
    selects the travel direction for the pan pattern.

    Returns the output path on success; raises RuntimeError on failure.
    """
    if pattern not in PATTERNS:
        raise ValueError(f"Unknown ffmpeg pattern: {pattern}")
    src = _resolve_input(image_path)
    if src is None:
        raise FileNotFoundError(f"Image not found: {image_path}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    vf = _build_filter(pattern, width, height, duration_s, fps,
                       focus_x=focus_x, focus_y=focus_y,
                       pan_direction=pan_direction)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(src),
        "-vf", vf,
        "-t", str(duration_s),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(out),
    ]
    logger.info("FFmpeg still clip: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=max(60, int(duration_s) * 20)
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed for {src.name}: {proc.stderr[-2000:]}")
    return str(out)


def generate_still_clip_batch(
    *,
    image_paths: list[str],
    pattern: str = "ken_burns_zoom",
    duration_s: float = 5.0,
    fps: int = 25,
    width: int = 1280,
    height: int = 720,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    pan_direction: str = "left-to-right",
    folder_name: str = "Videos",
    subfolder_name: Optional[str] = None,
) -> list[dict]:
    """Convert many stills to clips, registering each output as a Document.

    Returns a list of per-image result dicts:
      { source, filename, video_path, document_id, success, error }
    One image that fails doesn't abort the rest.
    """
    if not ffmpeg_available():
        raise RuntimeError(
            "ffmpeg not found on PATH. Install ffmpeg (brew install ffmpeg)."
        )

    batch_id = subfolder_name or f"FFmpeg_{uuid.uuid4().hex[:8]}"
    out_dir = FFMPEG_DIR / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, image_path in enumerate(image_paths):
        src = _resolve_input(image_path)
        ext = ".mp4"
        stem = (src.stem if src else f"clip_{i}")
        # Resolve "random" to a concrete direction for THIS image so the batch
        # gets variety instead of one pan direction for everything. The resolved
        # value is recorded in the result + metadata below.
        resolved_dir = pan_direction
        if pan_direction == "random":
            resolved_dir = random.choice(list(PAN_DIRECTIONS.keys()))
        out_path = out_dir / f"{stem}_{pattern}_{resolved_dir}{ext}"
        try:
            video_path = generate_still_clip(
                image_path=image_path,
                output_path=str(out_path),
                pattern=pattern,
                duration_s=duration_s,
                fps=fps,
                width=width,
                height=height,
                focus_x=focus_x,
                focus_y=focus_y,
                pan_direction=resolved_dir,
            )
            doc = register_file(
                physical_path=video_path,
                folder_name=folder_name,
                filename=out_path.name,
                subfolder_name=batch_id,
                file_type=".mp4",
                file_metadata={
                    "source": str(src),
                    "pattern": pattern,
                    "pan_direction": resolved_dir,
                    "duration_s": duration_s,
                    "focus": {"x": focus_x, "y": focus_y},
                    "ffmpeg_clip": True,
                },
            )
            results.append(
                {
                    "source": str(src),
                    "filename": out_path.name,
                    "video_path": video_path,
                    "document_id": doc.id if doc else None,
                    "success": True,
                    "error": None,
                    "pattern": pattern,
                    "pan_direction": resolved_dir,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("FFmpeg clip failed for %s: %s", image_path, e)
            results.append(
                {
                    "source": image_path,
                    "filename": None,
                    "video_path": None,
                    "document_id": None,
                    "success": False,
                    "error": str(e),
                    "pattern": pattern,
                    "pan_direction": resolved_dir,
                }
            )
    return results
