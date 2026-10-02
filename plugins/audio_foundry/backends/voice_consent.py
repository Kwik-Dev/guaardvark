"""Consent records for voice cloning.

Chatterbox clones a voice from a few seconds of speech, so a reference clip is
only used when the person who imported it confirmed they have the right to
clone that voice. The confirmation is stored beside the clip as
``<clip>.consent``: a JSON record of what was confirmed, when, and the SHA-256
of the recording it covers. Only the Audio Studio's consent step writes one
(POST /api/audio-foundry/voice-clips/upload with the confirmation, or
POST /api/audio-foundry/voice-clips/<id>/consent); importing a clip without it,
as the Video page's audio guide does, leaves the clip uncloneable. Withdrawing
consent (DELETE /api/audio-foundry/voice-clips/<id>/consent) removes the
record and keeps the clip; deleting the clip removes both.

The same check runs from this one module in two places: the backend's
/api/audio-foundry/generate/voice proxy, and this plugin where the clone
happens (``ChatterboxBackend.generate``). A process on this machine that calls
the plugin directly gets the same answer as the Studio.

A clip may be cloned when all of these hold:

* it resolves, symlinks followed, to a regular audio file inside
  ``<uploads>/voice_references``;
* ``<clip>.consent`` is a regular file holding a record of kind ``voice_clone``;
* the record's ``sha256`` matches the clip's bytes now, so a different
  recording saved under a consented name is not cloned.

An empty ``.consent`` file, which every upload used to create without asking,
is not a record and does not count.

Standard library only: the backend loads this file by path, outside the
plugin's venv.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

PathLike = Union[str, "os.PathLike[str]"]

CONSENT_SUFFIX = ".consent"
RECORD_KIND = "voice_clone"
REFERENCES_SUBDIR = "voice_references"
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus"})

# What the person confirms. The Studio shows this text and the record stores
# it, so a record says what was agreed to, not only that a box was ticked.
STATEMENT = (
    "I have the right to clone this voice: it is my own, or the person speaking "
    "agreed to have their voice cloned."
)

# A record is a few hundred bytes; anything larger is not one.
_MAX_RECORD_BYTES = 64 * 1024

# plugins/audio_foundry/backends/voice_consent.py -> the checkout root.
_CHECKOUT_ROOT = Path(__file__).resolve().parents[3]


class ConsentRequired(PermissionError):
    """The reference clip may not be cloned; the message says why."""


def uploads_dir() -> Path:
    """The uploads folder, found the way backend/config.py finds it.

    ``GUAARDVARK_UPLOAD_DIR`` (relative to ``GUAARDVARK_ROOT`` when it is not
    absolute), else ``<GUAARDVARK_ROOT>/data/uploads``. The plugin's start.sh
    loads the project .env and sets GUAARDVARK_ROOT, so both processes agree.
    """
    root = Path(os.environ.get("GUAARDVARK_ROOT") or _CHECKOUT_ROOT)
    configured = Path(os.environ.get("GUAARDVARK_UPLOAD_DIR") or "data/uploads")
    return configured if configured.is_absolute() else root / configured


def references_dir(upload_dir: Optional[PathLike] = None) -> Path:
    """``<uploads>/voice_references``; ``upload_dir`` defaults to :func:`uploads_dir`."""
    return Path(upload_dir if upload_dir is not None else uploads_dir()) / REFERENCES_SUBDIR


def record_path(clip: PathLike) -> Path:
    clip = Path(clip)
    return clip.with_name(clip.name + CONSENT_SUFFIX)


def sha256_of(path: PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_clip(ref: PathLike, ref_dir: PathLike) -> Path:
    """The clip ``ref`` names, as a real path inside ``ref_dir``.

    ``ref`` is absolute or relative to ``ref_dir``. Containment is decided on
    real paths before the file is looked at, so a refusal for a path outside
    the folder says nothing about what exists there.
    """
    base = os.path.realpath(os.fspath(ref_dir))
    raw = os.fspath(ref).strip()
    if not raw:
        raise ConsentRequired("No reference clip was named.")
    real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    # Both sides are real, normalised paths, so a prefix test with the
    # separator appended is exact: /refs2 does not pass for /refs.
    if real == base or not real.startswith(base.rstrip(os.sep) + os.sep):
        raise ConsentRequired(
            "A reference clip must be one imported in Audio Studio (its voice references "
            "folder); other files are never cloned."
        )
    clip = Path(real)
    if clip.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ConsentRequired(f"'{clip.name}' is not an audio clip.")
    try:
        if not stat.S_ISREG(os.stat(clip).st_mode):
            raise ConsentRequired(f"'{clip.name}' is not an audio clip.")
    except FileNotFoundError:
        raise ConsentRequired(f"Reference clip '{clip.name}' is not in Audio Studio's voice references.") from None
    return clip


def read_record(clip: PathLike) -> Optional[dict[str, Any]]:
    """The clip's consent record, or None when it has none.

    An empty or unreadable ``.consent`` file, or one that is a symlink, is not
    a record.
    """
    path = record_path(clip)
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RECORD_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.loads(fh.read() or "null")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def consent_problem(clip: PathLike) -> Optional[str]:
    """Why ``clip`` (already located) may not be cloned, or None when it may."""
    record = read_record(clip)
    if record is None or record.get("kind") != RECORD_KIND or not record.get("sha256"):
        return "no consent has been recorded for it"
    try:
        if record["sha256"] != sha256_of(clip):
            return "the recording changed after consent was recorded"
    except OSError:
        return "the recording could not be read"
    return None


def has_consent(clip: PathLike) -> bool:
    return consent_problem(clip) is None


def require_consent(ref: PathLike, ref_dir: Optional[PathLike] = None) -> Path:
    """The real path of a clip that may be cloned; raise ConsentRequired otherwise."""
    clip = locate_clip(ref, ref_dir if ref_dir is not None else references_dir())
    problem = consent_problem(clip)
    if problem:
        raise ConsentRequired(
            f"Voice clip '{clip.name}' cannot be cloned: {problem}. Confirm that you have "
            "the right to clone this voice in Audio Studio (Voice, reference clip), then try again."
        )
    return clip


def remove_record(clip: PathLike) -> bool:
    """Withdraw consent for ``clip`` by removing its ``.consent`` file.

    Returns whether the clip could be cloned just before. The clip itself is
    left alone. A ``.consent`` file that is not a record (empty, unreadable,
    a symlink) is removed too; a symlink is removed itself, never its target.
    """
    had_consent = has_consent(clip)
    path = record_path(clip)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return had_consent
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(path)
    return had_consent


def build_record(clip: PathLike, *, source: str) -> dict[str, Any]:
    clip = Path(clip)
    return {
        "kind": RECORD_KIND,
        "clip": clip.name,
        "sha256": sha256_of(clip),
        "statement": STATEMENT,
        "source": source,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_record(clip: PathLike, *, source: str) -> dict[str, Any]:
    """Record consent for ``clip`` (a located clip) and return the record.

    Written to a temporary file and renamed into place, so a reader never
    sees half a record.
    """
    clip = Path(clip)
    record = build_record(clip, source=source)
    target = record_path(clip)
    fd, tmp = tempfile.mkstemp(prefix=".consent-", dir=str(clip.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return record
