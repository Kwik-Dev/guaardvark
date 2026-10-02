"""Signed links to the agent's screen captures.

Captures are served from ``GET /api/tools/screenshots/<path>`` and their file
names follow the capture time, so they can be guessed. Chat shows them with a
plain ``<img>``, which cannot send the API key, so every link the backend hands
out carries a signature for that one path instead: ``?sig=`` is an
HMAC-SHA256 of the relative path under a secret this install generates once.
``auth_guard`` serves a request that carries a valid signature, and otherwise
applies its usual rule (this machine, or the API key).

A link does not expire: it is a capability for one file. Deleting
``data/.screenshot_url_secret`` revokes every link handed out so far; the next
link signed creates a new secret, and chat history is re-signed each time it is
served (``sign_screenshot_urls``), so saved conversations keep their pictures.
``SECRET_KEY`` is not used because it has a known development default.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode

logger = logging.getLogger(__name__)

ROUTE_PREFIX = "/api/tools/screenshots/"
SIGNATURE_PARAM = "sig"
SECRET_FILE_NAME = ".screenshot_url_secret"
# Binds a signature to this use of the secret, so the same secret could sign
# something else later without one signature standing in for the other.
_DOMAIN = b"guaardvark-screenshot-url-v1\n"

# A screenshot link inside any text: the route, the path up to whatever ends a
# URL in markdown, HTML or prose, and an optional query string.
_URL_RE = re.compile(
    re.escape(ROUTE_PREFIX)
    + r"(?P<path>[^\s?#()\[\]\"'<>\\]+)"
    + r"(?P<query>\?[^\s#()\[\]\"'<>\\]*)?"
)

_lock = threading.Lock()
_cached: Optional[tuple[tuple[int, int, int, int], bytes]] = None


def secret_path(storage_dir: Optional[Path] = None) -> Path:
    if storage_dir is None:
        try:
            from backend.config import STORAGE_DIR
            storage_dir = Path(STORAGE_DIR)
        except Exception:
            storage_dir = Path(__file__).resolve().parents[2] / "data"
    return Path(storage_dir) / SECRET_FILE_NAME


def _stat_key(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def _read_secret(path: Path) -> Optional[bytes]:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        value = bytes.fromhex(raw)
    except ValueError:
        return None
    return value if len(value) >= 32 else None


def _create_secret(path: Path) -> None:
    """Write a new secret, 0600 from the first byte. Loses a race gracefully:
    whichever process creates the file first wins and the other reads it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(secrets.token_bytes(32).hex() + "\n")


def _secret(storage_dir: Optional[Path] = None) -> bytes:
    """The signing secret, created on first use. Re-read when the file changes,
    so replacing or deleting it takes effect without a restart."""
    global _cached
    path = secret_path(storage_dir)
    with _lock:
        try:
            st = path.stat()
        except FileNotFoundError:
            _create_secret(path)
            st = path.stat()
        key = _stat_key(st)
        if _cached is not None and _cached[0] == key:
            return _cached[1]
        value = _read_secret(path)
        if value is None:
            # Unreadable or damaged: replace it. Links signed with it are lost,
            # which is what rotating the secret means anyway.
            logger.warning("Screenshot link secret at %s was unusable; creating a new one", path)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            _create_secret(path)
            st = path.stat()
            key = _stat_key(st)
            value = _read_secret(path)
            if value is None:
                raise RuntimeError(f"could not create a screenshot link secret at {path}")
        _cached = (key, value)
        return value


def signature(rel_path: str, storage_dir: Optional[Path] = None) -> str:
    """The signature for one path relative to the screenshots folder."""
    digest = hmac.new(_secret(storage_dir), _DOMAIN + rel_path.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def signature_valid(rel_path: str, sig: Optional[str], storage_dir: Optional[Path] = None) -> bool:
    if not rel_path or not sig:
        return False
    try:
        expected = signature(rel_path, storage_dir)
    except (OSError, RuntimeError) as exc:
        logger.warning("Screenshot link secret unavailable, refusing the signature: %s", exc)
        return False
    return hmac.compare_digest(expected.encode("ascii"), sig.encode("utf-8", "surrogateescape"))


def screenshot_url(rel_path: str, storage_dir: Optional[Path] = None) -> str:
    """The link to hand out for a capture saved under the screenshots folder.

    If the secret cannot be read or created (a read-only data folder), the link
    goes out unsigned and works where the route's usual rule lets it.
    """
    rel_path = rel_path.lstrip("/")
    url = f"{ROUTE_PREFIX}{quote(rel_path, safe='/')}"
    try:
        return f"{url}?{SIGNATURE_PARAM}={signature(rel_path, storage_dir)}"
    except (OSError, RuntimeError) as exc:
        logger.warning("Screenshot link secret unavailable, handing out %s unsigned: %s", url, exc)
        return url


def _resign(match: re.Match, storage_dir: Optional[Path]) -> str:
    rel_path = unquote(match.group("path"))
    query = match.group("query") or ""
    kept = [(k, v) for k, v in parse_qsl(query[1:], keep_blank_values=True) if k != SIGNATURE_PARAM]
    kept.append((SIGNATURE_PARAM, signature(rel_path, storage_dir)))
    return f"{ROUTE_PREFIX}{quote(rel_path, safe='/')}?{urlencode(kept)}"


def sign_screenshot_urls(value: Any, storage_dir: Optional[Path] = None) -> Any:
    """``value`` with every screenshot link in it signed with the current secret.

    Walks strings, lists, tuples and dicts (keys are left alone) and returns a
    new structure; anything else is returned as it is. A link that already has a
    signature gets the current one, so history saved before a secret change, or
    before links were signed at all, still loads.
    """
    if isinstance(value, str):
        if ROUTE_PREFIX not in value:
            return value
        try:
            return _URL_RE.sub(lambda m: _resign(m, storage_dir), value)
        except (OSError, RuntimeError) as exc:
            logger.warning("Screenshot link secret unavailable, links left as saved: %s", exc)
            return value
    if isinstance(value, dict):
        return {k: sign_screenshot_urls(v, storage_dir) for k, v in value.items()}
    if isinstance(value, list):
        return [sign_screenshot_urls(v, storage_dir) for v in value]
    if isinstance(value, tuple):
        return tuple(sign_screenshot_urls(v, storage_dir) for v in value)
    return value
