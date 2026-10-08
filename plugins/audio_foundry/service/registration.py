"""Register generated audio files with the main Guaardvark backend.

The backend exposes POST /api/outputs/register (backend/api/outputs_api.py) which
calls backend.services.output_registration.register_file() inside the Flask app
context — something the plugin service can't do itself because it runs in a
separate Python process.

Failure is non-fatal: the file is already on disk either way. We log and move on.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from backends.base import GenerationResult
from service.config_loader import resolve_backend_url

logger = logging.getLogger(__name__)

_DEFAULT_FOLDER = "Audio"

# Characters the Files rename route refuses, plus control characters.
_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_NAME_WORDS = 8
_NAME_MAX_CHARS = 60


def spoken_display_name(text: str | None, suffix: str) -> str:
    """A Files display name for a spoken clip: its first words, e.g. "Hello and welcome.wav".

    The file on disk keeps its unique id name; the backend adds " (2)" when a
    sibling already holds this name.
    """
    words = _UNSAFE_NAME_CHARS.sub(" ", text or "").split()[:_NAME_WORDS]
    name = " ".join(words)
    if len(name) > _NAME_MAX_CHARS:
        name = name[:_NAME_MAX_CHARS].rsplit(" ", 1)[0]
    name = name.strip(" .,;!-")
    return f"{name or 'Voice'}{suffix}"


def register_output(
    result: GenerationResult,
    backend_url: str | None = None,
    folder: str = _DEFAULT_FOLDER,
    timeout_s: float = 5.0,
    subfolder: str | None = None,
    filename: str | None = None,
) -> dict[str, Any] | None:
    """POST the file path + metadata to the backend, return the Document dict or None.

    The backend uses the file's actual location on disk — we don't move it here.
    `folder` must match one of the DEFAULT_FOLDERS the backend knows about;
    `subfolder` (created on demand) should be the directory the file was written
    to under it. `filename` is the display name; None shows the file's own name.
    `backend_url=None` resolves via config_loader.resolve_backend_url().
    """
    backend_url = resolve_backend_url(backend_url)
    payload = {
        "physical_path": str(Path(result.path).resolve()),
        "folder_name": folder,
        "file_metadata": result.meta,
    }
    if subfolder:
        payload["subfolder_name"] = subfolder
    if filename:
        payload["filename"] = filename
    try:
        response = httpx.post(
            f"{backend_url.rstrip('/')}/api/outputs/register",
            json=payload,
            timeout=timeout_s,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(
            "Registration POST failed (non-fatal, file remains at %s): %s",
            result.path, e,
        )
        return None

    body = response.json()
    # The backend wraps data in success_response format: {success, message, data}
    return body.get("data")
