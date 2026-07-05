"""Lightweight consistency / quality metrics for generated video + post-LoRA smoke.

This is a *starter* module per PIPELINES_IMPROVEMENTS.md (item #5).
Goal: give signal on identity preservation (for cast subjects), motion, and artifact rates
without heavy new dependencies. All functions are best-effort and fail open.

Intended call sites:
- lora_posttrain_smoke after the SDXL smoke still
- batch_video_generator on completion of cinematic items
- music_video / film crew final steps

Store results in job metadata or sidecar JSON next to the asset.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def compute_basic_video_stats(video_path: str | Path) -> Dict[str, Any]:
    """Return cheap stats: duration (if ffprobe), size, rough black % via re-use of existing logic.
    Never raises; returns partial dict on any failure.
    """
    p = Path(video_path)
    stats: Dict[str, Any] = {"path": str(p), "exists": p.exists()}
    if not p.exists():
        return stats
    try:
        stats["size_bytes"] = p.stat().st_size
    except Exception:
        pass

    # Reuse the spirit of the blank-video checker without duplicating ffmpeg blackdetect.
    # For now just note existence + size. Full black % can be added by caller if needed.
    try:
        import subprocess
        import re
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, timeout=15
        )
        if proc.returncode == 0:
            dur = float(proc.stdout.strip() or 0)
            stats["duration_s"] = round(dur, 2)
    except Exception:
        pass
    return stats


def score_identity_preservation(
    ref_paths: list[str],
    candidate_path: str,
    *,
    method: str = "size",  # "size" | "clip" | "face" (face requires optional dep)
) -> Dict[str, Any]:
    """Best-effort identity score between training refs and a generated candidate (smoke or keyframe).

    Current implementation: trivial baseline (file size similarity + count). 
    Future: embed refs + candidate with a small vision encoder (CLIP ViT or face model)
    available in the torch or main venv and compute cosine.

    Returns dict with "score" in [0,1] (higher=better) + "method" + "details".
    """
    result = {"method": method, "score": 0.5, "details": {}}
    try:
        cand = Path(candidate_path)
        if not cand.exists():
            result["details"]["error"] = "candidate missing"
            return result

        if method == "size":
            sizes = []
            for rp in ref_paths:
                try:
                    sizes.append(Path(rp).stat().st_size)
                except Exception:
                    pass
            if sizes:
                avg_ref = sum(sizes) / len(sizes)
                cand_size = cand.stat().st_size
                # crude: within 30% of average ref size is "reasonable"
                ratio = min(cand_size, avg_ref) / max(cand_size, avg_ref) if max(cand_size, avg_ref) else 0
                result["score"] = max(0.3, min(0.95, ratio))
                result["details"] = {"avg_ref_size": int(avg_ref), "cand_size": cand_size, "ratio": round(ratio, 3)}
        # TODO (future): implement real embedding path when a small vision model is guaranteed.
        # Example skeleton (guarded):
        # if method == "clip":
        #     from PIL import Image
        #     ... load tiny CLIP or use backend vision model ...
    except Exception as e:
        logger.debug("identity score computation skipped: %s", e)
        result["details"]["error"] = str(e)[:200]
    return result


def compute_frame_consistency(video_path: str | Path, sample_frames: int = 5) -> Dict[str, Any]:
    """Cheap temporal consistency proxy: sample a few frames and measure avg pixel diff.
    Higher variance can indicate jitter or good motion — use together with other signals.
    Requires imageio or PIL + opencv optional.
    """
    out: Dict[str, Any] = {"samples": 0, "mean_abs_diff": None}
    try:
        import numpy as np
        from PIL import Image
        p = Path(video_path)
        if not p.exists():
            return out
        # Very rough: use ffmpeg to extract a few frames to /tmp, diff them.
        # To stay light we just report that we could open the container.
        out["samples"] = sample_frames
        out["mean_abs_diff"] = "not_implemented"  # placeholder for real diff pipeline
    except Exception as e:
        logger.debug("frame consistency skipped: %s", e)
    return out


def annotate_asset(asset_path: str | Path, metrics: Dict[str, Any]) -> None:
    """Write/append a .metrics.json sidecar next to the asset for later inspection."""
    try:
        p = Path(asset_path)
        side = p.with_suffix(p.suffix + ".metrics.json")
        import json
        existing = {}
        if side.exists():
            try:
                existing = json.loads(side.read_text())
            except Exception:
                pass
        existing.update(metrics)
        side.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        logger.debug("could not write metrics sidecar for %s: %s", asset_path, e)


# Convenience for smoke test callers
def score_smoke_vs_refs(ref_image_paths: list[str], smoke_path: str) -> Dict[str, Any]:
    stats = compute_basic_video_stats(smoke_path)  # works for png too (size only)
    ident = score_identity_preservation(ref_image_paths, smoke_path, method="size")
    return {"stats": stats, "identity": ident}
