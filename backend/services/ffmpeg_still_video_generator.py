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

import json
import logging
import os
import random
import re
import shutil
import subprocess
import uuid
from datetime import datetime
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

# How the image is framed onto the output video.
#   fit    : letterbox — image keeps its size (never upscaled), centered on black.
#   cover  : zoom to fill — image is scaled to cover the frame and center-cropped.
#   native : output video size = the image's own size, clamped to [min, max].
FRAMING_MODES = {
    "fit": "Letterbox (keep size)",
    "cover": "Zoom to fill (crop)",
    "native": "Match image size",
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
FFMPEG_DIR = Path(UPLOAD_DIR) / "Videos" / "FFmpeg"  # legacy/CLI default root


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


def _build_filter(pattern: str, frame_w: int, frame_h: int, content_w: int,
                  content_h: int, framing: str, duration_s: float, fps: int,
                  focus_x: float = 0.5, focus_y: float = 0.5,
                  pan_direction: str = "left-to-right") -> str:
    """Return the ffmpeg filtergraph for the given motion pattern + framing.

    `frame_w x frame_h` is the output video frame size. `content_w x content_h`
    is the size the image content is rendered at before framing:
      - fit    : content is the image fitted inside the frame (native if small,
                 downscaled if large); it is centered on a black frame (letterbox).
      - cover  : content is the frame size; the image is scaled to COVER the frame
                 and center-cropped (zoom to fill).
      - native : content == frame == the image's own size (clamped to min/max).

    Transparency is flattened onto black first: a PNG's fully-transparent pixels
    keep their stored RGB (often white) and would otherwise show up as ugly white
    edges once the alpha channel is dropped at encode time. Compositing the
    scaled image over a black canvas turns those regions black.

    focus_x / focus_y (0.0–1.0) pick the point the camera keeps centered while
    zooming, and the fixed (non-moving) axis for a pan (default 0.5 = center).
    pan_direction selects the travel direction for the ken_burns_pan pattern.
    """
    fx = max(0.0, min(1.0, focus_x))
    fy = max(0.0, min(1.0, focus_y))
    frames = max(1, int(round(duration_s * fps)))

    if framing == "cover":
        # Zoom to fill: scale the image to COVER the frame, center-crop, and
        # flatten transparency onto a black frame-sized canvas.
        prologue = (
            f"color=c=black:s={frame_w}x{frame_h}[bg0];"
            f"[0:v]scale={frame_w}:{frame_h}:force_original_aspect_ratio=increase,"
            f"setsar=1,format=rgba[img0];"
            f"[bg0][img0]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2:format=auto[flat]"
        )
    else:
        # fit / native: flatten source transparency onto black at content size.
        prologue = (
            f"color=c=black:s={content_w}x{content_h}[bg0];"
            f"[0:v]scale={content_w}:{content_h},setsar=1,format=rgba[img0];"
            f"[bg0][img0]overlay=0:0:format=auto[flat]"
        )

    if pattern == "static":
        if framing == "fit":
            # Center the opaque content on a black target-size canvas (letterbox).
            body = f"[flat]pad={frame_w}:{frame_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps}"
        else:  # cover / native — content already fills the frame.
            body = f"[flat]setsar=1,fps={fps}"
        return f"{prologue};{body}"

    if pattern == "ken_burns_zoom":
        # Slow steady zoom from 1.0 to _KB_ZOOM_MAX while keeping the focus point
        # centered in the crop. x/y are the crop top-left in the (scaled-up)
        # input; locking the crop center on (fx*iw, fy*ih) keeps the focus steady.
        max_zoom = 1.35
        step = (max_zoom - 1.0) / frames
        z = f"min({step:.6f}+zoom,{max_zoom:.4f})"
        x = f"{fx:.4f}*iw-iw/(2*zoom)"
        y = f"{fy:.4f}*ih-ih/(2*zoom)"
        work_w = max(2, round(content_w * max_zoom))
        motion = (f"[flat]scale={work_w}:-1,"
                  f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={content_w}x{content_h}:fps={fps},"
                  f"setsar=1")
        if framing == "fit":
            body = f"{motion},pad={frame_w}:{frame_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        else:
            body = motion
        return f"{prologue};{body}"
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
        work_w = max(2, round(content_w * zoom))
        motion = (f"[flat]scale={work_w}:-1,"
                  f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={content_w}x{content_h}:fps={fps},"
                  f"setsar=1")
        if framing == "fit":
            body = f"{motion},pad={frame_w}:{frame_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        else:
            body = motion
        return f"{prologue};{body}"
    raise ValueError(f"Unknown ffmpeg pattern: {pattern}")


def _probe_dimensions(path: str) -> Optional[tuple[int, int]]:
    """Return (width, height) of an image/video via ffprobe, or None on failure."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        line = (proc.stdout.strip().splitlines() or [""])[0]
        parts = line.split("x")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
    except Exception:  # noqa: BLE001
        pass
    return None


def _clamped_size(sw: int, sh: int, target_w: int, target_h: int) -> tuple[int, int]:
    """Scale an image size so it fits inside the target frame proportionally,
    but NEVER upscales: small images keep their native size, and images larger
    than the target are scaled down to fit. The content is later padded onto the
    (even-sized) target frame, so the returned size itself need not be even."""
    ratio = min(1.0, target_w / sw, target_h / sh)
    return max(1, round(sw * ratio)), max(1, round(sh * ratio))


def _parse_size(size) -> Optional[tuple[int, int]]:
    """Parse a size like "1280x720" (or a bare int "720") into (w, h).
    Returns None for falsy / 0 / "auto" / "none" (meaning "no limit")."""
    if not size:
        return None
    if isinstance(size, (int, float)):
        s = int(size)
        return (s, s) if s > 0 else None
    s = str(size).strip().lower()
    if not s or s in ("0", "none", "auto"):
        return None
    parts = re.split(r"[x\s,]+|x", s)
    try:
        w = int(parts[0])
        h = int(parts[1]) if len(parts) > 1 else w
        if w > 0 and h > 0:
            return (w, h)
    except (ValueError, IndexError):
        pass
    return None


def _native_size(sw: int, sh: int, min_w: int, min_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Output frame = the image's native size, clamped to [min, max] preserving
    aspect ratio. Images between min and max keep their native size; images larger
    than max are downscaled to fit; images smaller than min are upscaled to reach
    the minimum. min_w/min_h of 0 means no lower bound."""
    upper = min(max_w / sw, max_h / sh)
    lower = max(min_w / sw, min_h / sh)
    ratio = min(max(1.0, lower), upper)
    ratio = max(0.0, ratio)
    return max(1, round(sw * ratio)), max(1, round(sh * ratio))


def generate_still_clip(
    *,
    image_path: str,
    output_path: str,
    pattern: str = "ken_burns_zoom",
    duration_s: float = 5.0,
    fps: int = 25,
    width: int = 1280,
    height: int = 720,
    framing: str = "fit",
    min_size=None,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    pan_direction: str = "left-to-right",
) -> str:
    """Convert one still image to a video clip with the given camera pattern.

    `framing` selects how the image is placed on the output frame:
      - "fit"    : letterbox — image keeps its size (never upscaled), centered on black.
      - "cover"  : zoom to fill — image scaled to cover the frame and center-cropped.
      - "native" : output video size = the image's own size, clamped to [min_size, width x height].
    `min_size` ("WxH" or int) is only used by "native" as the lower size bound.

    focus_x / focus_y (0.0–1.0) select the point the camera keeps centered while
    zooming, and the fixed axis for a pan (default 0.5 = center). pan_direction
    selects the travel direction for the pan pattern.

    Returns the output path on success; raises RuntimeError on failure.
    """
    if pattern not in PATTERNS:
        raise ValueError(f"Unknown ffmpeg pattern: {pattern}")
    if framing not in FRAMING_MODES:
        raise ValueError(f"Unknown ffmpeg framing: {framing}")
    src = _resolve_input(image_path)
    if src is None:
        raise FileNotFoundError(f"Image not found: {image_path}")

    dims = _probe_dimensions(str(src))
    frame_w, frame_h = width, height
    content_w, content_h = width, height
    if dims:
        sw, sh = dims
        if framing == "fit":
            # Keep small images native, downscale only if larger than the frame.
            content_w, content_h = _clamped_size(sw, sh, width, height)
        elif framing == "cover":
            # Content fills the frame (image is scaled to cover + cropped).
            content_w, content_h = width, height
        elif framing == "native":
            # Output frame = image size clamped to [min, max].
            min_w, min_h = _parse_size(min_size) or (0, 0)
            frame_w, frame_h = _native_size(sw, sh, min_w, min_h, width, height)
            content_w, content_h = frame_w, frame_h

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    vf = _build_filter(pattern, frame_w, frame_h, content_w, content_h, framing,
                       duration_s, fps, focus_x=focus_x, focus_y=focus_y,
                       pan_direction=pan_direction)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(src),
        # -filter_complex (not -vf) so the graph can branch into the black
        # background + overlay used to flatten source transparency onto black.
        "-filter_complex", vf,
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
    framing: str = "fit",
    min_size=None,
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

    # Batches live one level under Videos/<batch_id> so the video library's
    # list_batches() (which scans Videos one level deep) can discover them, and
    # we write a batch_metadata.json so it reports the correct video counts.
    batch_id = subfolder_name or f"FFmpeg_{uuid.uuid4().hex[:8]}"
    out_dir = Path(UPLOAD_DIR) / "Videos" / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)
    start_iso = datetime.now().isoformat()

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
                framing=framing,
                min_size=min_size,
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
                    "framing": framing,
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

    # Write batch metadata so the Video Library shows real counts + a nice name,
    # AND includes the per-clip results (so /status/<id> can play each clip).
    # The `results` array matches BatchVideoResult fields; video_path is the
    # filename relative to the batch dir, which /batch-video/video/<id>/<name>
    # serves.
    ok = sum(1 for r in results if r.get("success"))
    fail = len(results) - ok
    display = (PAN_DIRECTIONS.get(pattern, {}).get("label") or pattern)
    try:
        res_meta = []
        for idx, r in enumerate(results):
            res_meta.append({
                "item_id": f"{idx}",
                "success": bool(r.get("success")),
                "video_path": (r.get("filename") if r.get("success") else None),
                "frame_paths": [],
                "thumbnail_path": None,
                "error": r.get("error"),
                "metadata": {
                    "pattern": pattern,
                    "pan_direction": r.get("pan_direction"),
                    "source": r.get("source"),
                },
            })
        meta = {
            "batch_id": batch_id,
            "status": "completed",
            "total_videos": len(results),
            "completed_videos": ok,
            "failed_videos": fail,
            "start_time": start_iso,
            "end_time": datetime.now().isoformat(),
            "metadata": {"display_name": f"FFmpeg {display}", "ffmpeg_clip": True},
            "results": res_meta,
        }
        (out_dir / "batch_metadata.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write FFmpeg batch metadata: %s", e)

    return results
