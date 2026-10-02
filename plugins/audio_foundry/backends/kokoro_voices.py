"""The Kokoro voices Audio Foundry offers, and the files each one needs.

The list lives in ``kokoro_voices.json`` beside this module. The sidecar reads
it for GET /voices and to refuse an unknown voice id; the main backend reads
the same file (backend/services/audio_foundry_models.py) to decide whether
Kokoro counts as installed and to check voice ids from MCP clients. Adding a
voice here makes Manage models report Kokoro as not installed until its voice
pack is on disk, which is the point: Install fetches it, generation never does.

``english_g2p`` is spaCy's English pipeline. Kokoro's English phonemizer
(misaki ``en.G2P``) loads it for the American and British pipelines and runs
``spacy.cli.download`` when it is absent, which pip-installs from GitHub in the
middle of a generation request. The Manage-models Install puts the pinned
wheel into this plugin's venv instead.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("kokoro_voices.json")

# Every catalog id has this shape. Checking it first keeps paths ('x.pt',
# which KPipeline would torch.load) and blends ('a,b') out before any lookup.
VOICE_ID_PATTERN = re.compile(r"^[a-z]{2}_[a-z]+$")


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def hf_repo() -> str:
    return load_catalog()["hf_repo"]


def config_file() -> str:
    return load_catalog()["config_file"]


def weights_file() -> str:
    return load_catalog()["weights_file"]


def model_files() -> list[str]:
    return [config_file(), weights_file()]


def voice_ids() -> list[str]:
    return [v["id"] for g in load_catalog()["groups"] for v in g["voices"]]


def default_voice() -> str:
    return load_catalog()["default"]


def voice_file(voice_id: str) -> str:
    """Repo-relative path of a voice pack, e.g. ``voices/af_heart.pt``."""
    return load_catalog()["voice_file"].format(voice=voice_id)


def english_g2p() -> dict[str, Any]:
    return dict(load_catalog()["english_g2p"])


class UnknownVoice(ValueError):
    """The voice id is not one of the Kokoro voices this plugin offers."""


def check_voice_id(voice_id: str) -> str:
    """Return ``voice_id`` when it is a catalog voice, else raise UnknownVoice."""
    voice_id = (voice_id or "").strip()
    if not VOICE_ID_PATTERN.match(voice_id) or voice_id not in voice_ids():
        raise UnknownVoice(
            f"unknown Kokoro voice {voice_id!r}; choose one of: {', '.join(voice_ids())}"
        )
    return voice_id
