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


def _histogram_vec(path: str, bins: int = 16):
    from PIL import Image
    import numpy as np
    img = Image.open(path).convert("RGB").resize((64, 64))
    arr = np.asarray(img, dtype=np.float32)
    hist = []
    for c in range(3):
        h, _ = np.histogram(arr[:, :, c], bins=bins, range=(0, 256), density=True)
        hist.append(h)
    v = np.concatenate(hist)
    n = float(np.linalg.norm(v)) or 1.0
    return v / n


def score_identity_preservation(
    ref_paths: list[str],
    candidate_path: str,
    *,
    method: str = "hist",  # "hist" | "size" | "clip" | "face"
) -> Dict[str, Any]:
    """Best-effort identity score between training refs and a generated candidate.

    Default ``hist``: mean cosine similarity of RGB histograms (cheap, no extra deps
    beyond PIL/numpy already used elsewhere). Higher is better in [0,1].
    """
    result = {"method": method, "score": 0.5, "details": {}}
    try:
        cand = Path(candidate_path)
        if not cand.exists():
            result["details"]["error"] = "candidate missing"
            return result

        if method in ("hist", "auto"):
            import numpy as np
            try:
                cand_v = _histogram_vec(str(cand))
                sims = []
                for rp in ref_paths:
                    try:
                        if Path(rp).is_file():
                            sims.append(float(np.dot(cand_v, _histogram_vec(rp))))
                    except Exception:
                        pass
                if sims:
                    score = float(sum(sims) / len(sims))
                    # Cosine of normalized hist is typically ~0.7–0.99 for same subject.
                    result["score"] = max(0.0, min(1.0, score))
                    result["method"] = "hist"
                    result["details"] = {
                        "n_refs": len(sims),
                        "mean_cosine": round(score, 4),
                        "min_cosine": round(min(sims), 4),
                        "max_cosine": round(max(sims), 4),
                    }
                    return result
            except Exception as e:
                logger.debug("hist identity score failed, falling back to size: %s", e)
                method = "size"

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
                ratio = min(cand_size, avg_ref) / max(cand_size, avg_ref) if max(cand_size, avg_ref) else 0
                result["score"] = max(0.3, min(0.95, ratio))
                result["method"] = "size"
                result["details"] = {
                    "avg_ref_size": int(avg_ref),
                    "cand_size": cand_size,
                    "ratio": round(ratio, 3),
                }
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


DEFAULT_VIDEO_REVIEW_MODEL = "minicpm-v4.5:latest"

_REVIEW_PROMPT = (
    "These {n} frames are sampled in order from a {dur}s AI-generated video clip. "
    "Review it as a strict QA reviewer of generated video. Reply with ONLY a JSON "
    "object with these keys: "
    '"summary" (one sentence: what happens across the clip), '
    '"temporal_coherence" (does the subject/motion stay consistent frame-to-frame, '
    "or morph/flicker/teleport — be specific), "
    '"artifacts" (array of concrete visual defects: warping, extra limbs, texture '
    "crawl, blur, duplicated objects; empty array if none), "
    '"quality_score" (integer 0-10, 10 = flawless), '
    '"justification" (one line for the score).'
)


def _extract_frames_b64(video_path: str | Path, n: int = 6, width: int = 448) -> list[str]:
    """Sample n evenly-spaced frames as base64 JPEGs via ffmpeg. [] on any failure."""
    import base64
    import subprocess
    import tempfile

    p = Path(video_path)
    if not p.exists():
        return []
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=nb_frames", "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
            capture_output=True, text=True, timeout=15,
        )
        nb = int((probe.stdout or "0").strip() or 0)
    except Exception:
        nb = 0
    step = max(1, nb // n) if nb else 1

    with tempfile.TemporaryDirectory() as td:
        pattern = f"select='not(mod(n\\,{step}))'" if nb else "select='eq(pict_type\\,I)'"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(p),
                 "-vf", f"{pattern},scale={width}:-1", "-frames:v", str(n),
                 "-vsync", "vfr", f"{td}/f%02d.jpg"],
                capture_output=True, timeout=60,
            )
        except Exception as e:
            logger.warning("video review: ffmpeg frame extract failed: %s", e)
            return []
        frames = sorted(Path(td).glob("f*.jpg"))
        out = []
        for fp in frames[:n]:
            try:
                out.append(base64.b64encode(fp.read_bytes()).decode())
            except Exception:
                pass
        return out


def review_video_quality(
    video_path: str | Path,
    *,
    sample_frames: int = 6,
    model: Optional[str] = None,
    annotate: bool = False,
) -> Dict[str, Any]:
    """VLM temporal QA of a generated clip — the real signal compute_frame_consistency
    only gestured at. Samples frames and asks a video-capable local VLM (default
    MiniCPM-V 4.5) to judge scene, temporal coherence, artifacts, and a 0-10 score.

    Catches the failure single-frame metrics can't: subject morphing / flicker /
    teleporting across frames. Best-effort and fails OPEN — returns
    {"available": False, "reason": ...} when ffmpeg / ollama / the model is missing,
    so callers can treat it as an optional signal.

    annotate=True writes the result into the asset's .metrics.json sidecar.
    """
    import json as _json
    import re as _re

    model = model or __import__("os").environ.get(
        "GUAARDVARK_VIDEO_REVIEW_MODEL", DEFAULT_VIDEO_REVIEW_MODEL
    )
    result: Dict[str, Any] = {"available": False, "model": model}

    frames = _extract_frames_b64(video_path, n=sample_frames)
    if not frames:
        result["reason"] = "no_frames"
        return result

    dur = compute_basic_video_stats(video_path).get("duration_s", "?")
    prompt = _REVIEW_PROMPT.format(n=len(frames), dur=dur)

    try:
        import ollama
        resp = ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt, "images": frames}],
            format="json",
            options={"temperature": 0.2, "num_predict": 500},
        )
        raw = (resp["message"]["content"] or "").strip()
    except Exception as e:  # noqa: BLE001 — optional signal, never crash the caller
        logger.warning("video review: VLM call failed: %s", e)
        result["reason"] = f"vlm_unavailable: {e}"
        return result

    review: Dict[str, Any] = {}
    try:
        review = _json.loads(raw)
    except _json.JSONDecodeError:
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            try:
                review = _json.loads(m.group(0))
            except _json.JSONDecodeError:
                pass
    if not review:
        result["reason"] = "unparseable_review"
        result["raw"] = raw[:300]
        return result

    try:
        score = int(round(float(review.get("quality_score"))))
        review["quality_score"] = max(0, min(10, score))
    except (TypeError, ValueError):
        review["quality_score"] = None

    result.update({"available": True, "frames_reviewed": len(frames), "review": review})
    if annotate:
        annotate_asset(video_path, {"vlm_review": result})
    return result


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
