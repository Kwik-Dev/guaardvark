"""Encrypted-at-rest credential store for outbound connections.

Secrets for social accounts, AI providers and MCP servers live in a 0600 file
under the operator's config directory — never in Postgres, never in the repo,
never in a release archive. The database holds only non-secret metadata and a
``credential_ref`` pointing here.

Records are encrypted individually rather than as one blob so the inventory
(which refs exist, when they were written, which fields are set, when a token
expires) stays readable without the key. A missing or rotated key costs the
secret values but never the connection graph.

Environment variables remain a recognised read-only source: when a provider
declares ``env_keys`` and one is set in the process environment, it wins over
the file and is reported with ``source="env"`` so the UI can render it as
detected-but-unmanaged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Sequence

from backend.utils.plugin_secrets import is_masked_or_empty, mask_hint

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_DIR_MODE = 0o700
_FILE_MODE = 0o600

# Serialises writes within a process; flock() covers web-vs-worker processes.
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def config_dir() -> Path:
    """Credential home. ``GUAARDVARK_CONFIG_DIR`` overrides it for tests."""
    override = os.environ.get("GUAARDVARK_CONFIG_DIR", "").strip()
    return Path(override) if override else Path.home() / ".config" / "guaardvark"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def key_path() -> Path:
    return config_dir() / "secret.key"


def credential_ref(family: str, provider: str, account_slug: str = "default") -> str:
    """Build the store key for a connection."""
    return f"{family}:{provider}:{account_slug}"


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------
def _plaintext_requested() -> bool:
    return os.environ.get("GUAARDVARK_CREDENTIALS_PLAINTEXT", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _fernet():
    """Return a Fernet instance, or None when running in plaintext mode.

    Absence of the key must degrade to plaintext rather than lock the operator
    out of their own credentials.
    """
    if _plaintext_requested():
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "cryptography unavailable — credentials will be stored unencrypted."
        )
        return None

    path = key_path()
    try:
        if path.exists():
            return Fernet(path.read_bytes().strip())
        _ensure_dir()
        key = Fernet.generate_key()
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        logger.info("Generated credential encryption key at %s", path)
        return Fernet(key)
    except FileExistsError:
        return Fernet(path.read_bytes().strip())
    except Exception as e:  # noqa: BLE001 - never block on crypto setup
        logger.warning("Could not set up credential encryption (%s); using plaintext.", e)
        return None


def _ensure_dir() -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, _DIR_MODE)
    except OSError:
        pass
    return d


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def _empty_doc() -> Dict[str, Any]:
    return {"version": SCHEMA_VERSION, "records": {}}


def _read_doc() -> Dict[str, Any]:
    path = credentials_path()
    if not path.exists():
        return _empty_doc()
    try:
        doc = json.loads(path.read_text() or "{}")
    except (OSError, ValueError) as e:
        logger.error("Credential file unreadable (%s); treating as empty.", e)
        return _empty_doc()
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), dict):
        logger.error("Credential file malformed; treating as empty.")
        return _empty_doc()
    return doc


def _write_doc(doc: Dict[str, Any]) -> None:
    """Atomically replace the credential file, never widening its mode."""
    _ensure_dir()
    path = credentials_path()
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(doc, indent=2, sort_keys=True)

    with _write_lock:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        try:
            os.chmod(path, _FILE_MODE)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Environment overlay
# ---------------------------------------------------------------------------
def _env_value(env_keys: Sequence[str]) -> tuple[str, Optional[str]]:
    """First non-empty declared env var, as ``(value, env_key)``."""
    for key in env_keys or ():
        value = os.environ.get(key, "").strip()
        if value:
            return value, key
    return "", None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def set_secret(
    ref: str,
    values: Dict[str, str],
    *,
    hint_field: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> None:
    """Write (or replace) the secret bundle for *ref*."""
    clean = {k: v for k, v in (values or {}).items() if isinstance(v, str) and v.strip()}
    if not clean:
        raise ValueError("Refusing to store an empty credential bundle.")

    fernet = _fernet()
    blob = json.dumps(clean, sort_keys=True)
    hint_source = clean.get(hint_field) if hint_field else None
    if not hint_source:
        hint_source = next(iter(clean.values()))

    record = {
        "enc": "fernet" if fernet else "plain",
        "data": fernet.encrypt(blob.encode("utf-8")).decode("ascii") if fernet else clean,
        "hint": mask_hint(hint_source),
        "fields": sorted(clean.keys()),
        "updated_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
    }

    doc = _read_doc()
    doc["records"][ref] = record
    _write_doc(doc)
    logger.info("Stored credential %s (fields=%s)", ref, record["fields"])


def get_secret(ref: str, *, env_keys: Sequence[str] = (), env_field: str = "token") -> Dict[str, str]:
    """Resolve the secret bundle for *ref*. Environment wins over the file.

    Returns an empty dict when absent or undecryptable — callers treat that as
    "not configured" rather than an error.
    """
    value, env_key = _env_value(env_keys)
    if value:
        return {env_field: value}

    record = _read_doc()["records"].get(ref)
    if not record:
        return {}

    data = record.get("data")
    if record.get("enc") != "fernet":
        return dict(data) if isinstance(data, dict) else {}

    fernet = _fernet()
    if fernet is None:
        logger.warning("Credential %s is encrypted but no key is available.", ref)
        return {}
    try:
        return json.loads(fernet.decrypt(str(data).encode("ascii")).decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - a bad key must not raise into callers
        logger.error("Could not decrypt credential %s: %s", ref, e)
        return {}


def delete_secret(ref: str) -> bool:
    doc = _read_doc()
    if ref not in doc["records"]:
        return False
    doc["records"].pop(ref)
    _write_doc(doc)
    logger.info("Deleted credential %s", ref)
    return True


def list_refs() -> List[str]:
    return sorted(_read_doc()["records"].keys())


def get_status(ref: str, *, env_keys: Sequence[str] = ()) -> Dict[str, Any]:
    """Masked status for the UI. Never returns a secret value."""
    value, env_key = _env_value(env_keys)
    if value:
        return {
            "configured": True,
            "hint": mask_hint(value),
            "source": "env",
            "env_key": env_key,
            "fields": [],
            "encrypted": False,
            "expires_at": None,
        }

    record = _read_doc()["records"].get(ref)
    if not record:
        return {
            "configured": False,
            "hint": "",
            "source": "none",
            "env_key": None,
            "fields": [],
            "encrypted": False,
            "expires_at": None,
        }
    return {
        "configured": True,
        "hint": record.get("hint", ""),
        "source": "file",
        "env_key": None,
        "fields": record.get("fields", []),
        "encrypted": record.get("enc") == "fernet",
        "expires_at": record.get("expires_at"),
    }


def apply_secret_updates(
    ref: str,
    payload: MutableMapping[str, Any],
    field_names: Sequence[str],
    *,
    hint_field: Optional[str] = None,
    expires_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pop secret fields out of *payload* and persist the real values.

    Mutates *payload* in place so the caller can pass the remainder straight to
    the model. Blank or mask-echoed values are discarded without clearing the
    stored secret, so saving a form untouched keeps the existing credential.
    """
    result: Dict[str, Any] = {"updated": [], "skipped_empty": False}
    incoming: Dict[str, str] = {}
    saw_field = False

    for field in field_names:
        if field not in payload:
            continue
        saw_field = True
        raw = payload.pop(field)
        if is_masked_or_empty(raw):
            continue
        incoming[field] = raw.strip()

    if not incoming:
        result["skipped_empty"] = saw_field
        return result

    merged = dict(get_secret(ref))
    merged.update(incoming)
    set_secret(ref, merged, hint_field=hint_field, expires_at=expires_at)
    result["updated"] = sorted(incoming.keys())
    return result


def rotate_key() -> Dict[str, Any]:
    """Re-encrypt every record under a freshly generated key.

    The previous key is kept as ``secret.key.bak-<timestamp>`` so a botched
    rotation is recoverable.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return {"rotated": 0, "error": "cryptography is not installed"}

    doc = _read_doc()
    refs = list(doc["records"].keys())
    plaintext = {ref: get_secret(ref) for ref in refs}
    undecryptable = [ref for ref, values in plaintext.items() if not values]
    if undecryptable:
        return {
            "rotated": 0,
            "error": f"Cannot decrypt {len(undecryptable)} record(s); rotation aborted.",
            "refs": undecryptable,
        }

    old = key_path()
    if old.exists():
        backup = old.with_name(f"secret.key.bak-{datetime.now():%Y%m%d%H%M%S}")
        os.replace(old, backup)
        try:
            os.chmod(backup, _FILE_MODE)
        except OSError:
            pass

    _ensure_dir()
    fd = os.open(str(old), os.O_CREAT | os.O_EXCL | os.O_WRONLY, _FILE_MODE)
    try:
        os.write(fd, Fernet.generate_key())
    finally:
        os.close(fd)

    for ref, values in plaintext.items():
        record = doc["records"][ref]
        set_secret(
            ref,
            values,
            expires_at=(
                datetime.fromisoformat(record["expires_at"])
                if record.get("expires_at")
                else None
            ),
        )
    logger.info("Rotated credential key; re-encrypted %d record(s).", len(refs))
    return {"rotated": len(refs), "error": None}


def health() -> Dict[str, Any]:
    """Store diagnostics for the Connections page."""
    path = credentials_path()
    mode = None
    if path.exists():
        try:
            mode = oct(os.stat(path).st_mode & 0o777)
        except OSError:
            pass
    return {
        "path": str(path),
        "exists": path.exists(),
        "mode": mode,
        "key_present": key_path().exists(),
        "encrypted": key_path().exists() and not _plaintext_requested(),
        "record_count": len(_read_doc()["records"]),
    }
