"""Environment-driven configuration for the training-video engine.

Defaults target a native Guaardvark install, so the engine runs unchanged
wherever it is deployed. Override ``TD_API`` to drive a sibling install's
already-running GPU services instead of this one's.

Anything company-specific — palette, vocabulary, narrator — comes from the
selected project rather than from here. See ``project.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Backend exposing /api/batch-image and /api/batch-video.
API = os.environ.get("TD_API", "http://localhost:5000").rstrip("/")

# audio_foundry binds one port per machine regardless of which install owns it.
FOUNDRY = os.environ.get("TD_FOUNDRY", "http://127.0.0.1:8206").rstrip("/")


def _path(env: str, default: Path) -> Path:
    raw = os.environ.get(env)
    if not raw:
        return default
    p = Path(raw).expanduser()
    return p if p.is_absolute() else REPO / p


OUT_ROOT = _path("TD_OUT_ROOT", REPO / "data/outputs/training")
CACHE_ROOT = _path("TD_CACHE_ROOT", REPO / "data/cache/training_broll")

# Engine assets travel with the module so it works wherever it is copied.
ASSET_ROOT = Path(__file__).resolve().parent / "assets"

BACKEND_VENV_PY = _path("TD_BACKEND_PY", REPO / "backend/venv/bin/python")

# Read-check transcription. tiny.en misses trade vocabulary and copes badly
# with a band-limited reference voice; small.en reads both correctly.
WHISPER_MODEL = os.environ.get("TD_WHISPER_MODEL", "small.en")

# Still generation. Pinned rather than 'auto': the router resolves 'auto' to a
# family whose defaults override the requested frame size.
IMAGE_MODEL = os.environ.get("TD_IMAGE_MODEL", "zimage-turbo")
IMAGE_W = int(os.environ.get("TD_IMAGE_W", "1600"))
IMAGE_H = int(os.environ.get("TD_IMAGE_H", "896"))

# Image-to-video. WAN 2.2 is the verified default; LTX is opt-in.
VIDEO_MODEL = os.environ.get("TD_VIDEO_MODEL", "wan22-i2v")

FPS = int(os.environ.get("TD_FPS", "30"))
WIDTH = int(os.environ.get("TD_WIDTH", "1920"))
HEIGHT = int(os.environ.get("TD_HEIGHT", "1080"))
