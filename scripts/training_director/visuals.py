"""B-roll generation for training videos, via the Guaardvark HTTP API.

Stills come from `/api/batch-image`; optional motion from `/api/batch-video`.
Results are cached by prompt so re-running a production reuses approved frames
instead of paying for them again.

Prompt discipline — this is a safety constraint, not a style preference:
generated imagery is establishing and atmospheric ONLY. Every specification a
trainee acts on belongs on a spec card and in the narration. Prompts must
describe scene, material and light, never a countable technical detail (nail
counts, seam geometry, fastener spacing), because the model will render those
wrong and a trainee could read the frame as instruction.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import requests

from config import (API, CACHE_ROOT, FOUNDRY, IMAGE_H, IMAGE_MODEL, IMAGE_W,
                    VIDEO_MODEL)

CACHE_DIR = CACHE_ROOT
INDEX_FILE = CACHE_DIR / "index.json"

# Appended to every still prompt: holds the series look together and keeps
# rendered text out of the frame (all text is composited).
STYLE_SUFFIX = (
    "documentary photograph, natural daylight, realistic materials, "
    "shallow depth of field, no text, no watermark, no people facing camera"
)

NEGATIVE = "text, watermark, logo, caption, diagram, illustration, cartoon"


def _key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def _load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text())
    return {}


def _save_index(index: dict) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2, sort_keys=True))


def full_prompt(prompt: str) -> str:
    return f"{prompt.rstrip().rstrip(',')}, {STYLE_SUFFIX}"


def dispatch_stills(prompts: list[str], variants: int = 2) -> str:
    """Queue one batch covering `prompts`; returns the batch id."""
    expanded = [full_prompt(p) for p in prompts for _ in range(variants)]
    r = requests.post(f"{API}/api/batch-image/generate/prompts", json={
        "prompts": expanded,
        "model": IMAGE_MODEL,
        "width": IMAGE_W,
        "height": IMAGE_H,
        "negative_prompt": NEGATIVE,
        "style": "realistic",
    }, timeout=60)
    r.raise_for_status()
    return r.json()["data"]["batch_id"]


def batch_status(batch_id: str) -> dict:
    r = requests.get(f"{API}/api/batch-image/status/{batch_id}",
                     params={"include_results": "true"}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


_TERMINAL_OK = {"completed", "complete", "done", "finished"}
_TERMINAL_BAD = {"error", "failed", "cancelled", "canceled"}


def wait_for_batch(batch_id: str, timeout_s: int = 3600) -> list[Path]:
    """Block until the batch settles; returns the generated image paths in order."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = batch_status(batch_id)
        state = (data.get("status") or "").lower()
        done = data.get("completed_images", 0)
        total = data.get("total_images", 0)
        print(f"  batch {batch_id}: {state} {done}/{total}", flush=True)
        if state in _TERMINAL_OK:
            return _collect(data)
        if state in _TERMINAL_BAD:
            raise RuntimeError(
                f"batch {batch_id} ended '{state}': "
                f"{data.get('error') or 'no detail returned'}")
        time.sleep(10)
    raise RuntimeError(f"batch {batch_id} did not finish within {timeout_s}s")


def release_voice_vram() -> None:
    """Unload the voice model before an image batch.

    Chatterbox and the image models do not co-reside on a single consumer
    card; a batch that starts while the narrator is loaded fails its VRAM
    headroom check outright.
    """
    try:
        requests.post(f"{FOUNDRY}/evict/voice", timeout=60)
    except Exception as e:
        print(f"  could not evict the voice model ({e}) — continuing")


def stop_voice_service() -> None:
    """Stop audio_foundry outright to reclaim its CUDA context.

    Evicting a backend frees its weights but not the process's context, which
    is several hundred megabytes — enough to keep a large image model from
    clearing its headroom check. narration.ensure_narrator_ready restarts the
    service when the narration pass needs it.
    """
    try:
        requests.post(f"{API}/api/plugins/audio_foundry/stop", timeout=120)
        print("  stopped audio_foundry to free its CUDA context")
        time.sleep(3)
    except Exception as e:
        print(f"  could not stop audio_foundry ({e}) — continuing")


def _collect(data: dict) -> list[Path]:
    paths = [Path(r["image_path"]) for r in data.get("results") or []
             if r.get("success") and r.get("image_path")]
    paths = [p for p in paths if p.exists()]
    if paths:
        return paths
    out_dir = data.get("output_dir")
    if out_dir and Path(out_dir).exists():
        root = Path(out_dir)
        return sorted(root.glob("images/*.png")) or sorted(root.glob("*.png"))
    return []


# The service reports a VRAM shortfall as a batch error and invites a retry.
# The narrator's CUDA context survives eviction, so a shortfall of a few
# hundred megabytes clears on its own once the allocator settles.
_HEADROOM_HINTS = ("headroom", "free vram", "gpu short", "try again")
HEADROOM_RETRIES = 4
HEADROOM_WAIT_S = 45


def _dispatch_with_retry(prompts: list[str], variants: int) -> list[Path]:
    for attempt in range(1, HEADROOM_RETRIES + 1):
        batch_id = dispatch_stills(prompts, variants=variants)
        try:
            return wait_for_batch(batch_id)
        except RuntimeError as e:
            transient = any(h in str(e).lower() for h in _HEADROOM_HINTS)
            if not transient or attempt == HEADROOM_RETRIES:
                raise
            print(f"  VRAM not free yet (attempt {attempt}/{HEADROOM_RETRIES})"
                  f" — retrying in {HEADROOM_WAIT_S}s")
            # Waiting only helps a transient spike. A persistent shortfall of a
            # few hundred megabytes is the voice service's context, which has
            # to be stopped rather than evicted.
            if attempt == 1:
                release_voice_vram()
            else:
                stop_voice_service()
            time.sleep(HEADROOM_WAIT_S)
    raise RuntimeError("unreachable")


def stills_for(prompts: list[str], variants: int = 2) -> dict[str, list[Path]]:
    """Return cached-or-generated stills keyed by prompt.

    Only prompts absent from the cache are dispatched, so an approved look
    survives re-runs and script edits.
    """
    index = _load_index()
    missing = [p for p in prompts
               if not (index.get(_key(p))
                       and all(Path(f).exists() for f in index[_key(p)]))]
    if missing:
        print(f"generating {len(missing)} new still prompt(s) "
              f"x{variants} variants…")
        release_voice_vram()
        produced = _dispatch_with_retry(missing, variants)
        if len(produced) < len(missing):
            raise RuntimeError(
                f"batch returned {len(produced)} images for {len(missing)} "
                f"prompt(s) x{variants} variants — check the image service log")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for i, prompt in enumerate(missing):
            chunk = produced[i * variants:(i + 1) * variants]
            kept = []
            for j, src in enumerate(chunk):
                dst = CACHE_DIR / f"{_key(prompt)}_{j}{src.suffix}"
                dst.write_bytes(src.read_bytes())
                kept.append(str(dst))
            index[_key(prompt)] = kept
        _save_index(index)

    return {p: [Path(f) for f in index[_key(p)]] for p in prompts}


def animate(still: Path, prompt: str, dest: Path, seconds: float = 4.0) -> Path:
    """Render motion from a still via image-to-video. Falls back to the still.

    Motion is an enhancement: a failed I2V render must never block a production,
    because `assemble.py` gives every still a slow push anyway.
    """
    try:
        with open(still, "rb") as fh:
            r = requests.post(
                f"{API}/api/batch-video/generate/image",
                files={"image": (still.name, fh, "image/png")},
                data={"prompt": full_prompt(prompt), "model": VIDEO_MODEL,
                      "duration": str(seconds)},
                timeout=120)
        r.raise_for_status()
        batch_id = r.json()["data"]["batch_id"]
    except Exception as e:
        print(f"  i2v dispatch failed ({e}) — using the still")
        return still

    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        s = requests.get(f"{API}/api/batch-video/status/{batch_id}", timeout=30)
        data = s.json().get("data", {})
        state = (data.get("status") or "").lower()
        if state in ("completed", "complete", "done", "finished"):
            for r in data.get("results") or []:
                path = r.get("video_path") or r.get("output_path")
                if path and Path(path).exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(Path(path).read_bytes())
                    return dest
            break
        if state in ("failed", "cancelled"):
            break
        time.sleep(10)
    print(f"  i2v produced nothing for {still.name} — using the still")
    return still
