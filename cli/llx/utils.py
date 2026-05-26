import hashlib
import re
from pathlib import Path

MAX_FILE_MENTION_BYTES = 1024 * 1024
BLOCKED_EXTERNAL_ROOTS = ("/boot", "/dev", "/etc", "/proc", "/root", "/run", "/sys")
BLOCKED_PATH_SEGMENTS = {".aws", ".docker", ".gnupg", ".kube", ".ssh"}
SENSITIVE_SUFFIXES = {".key", ".kdbx", ".p12", ".pem", ".pfx"}
SENSITIVE_FILENAMES = {"id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"}
TRAILING_PUNCTUATION = ".,;:!?)］]}'\""


def _resolve_candidate(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _looks_like_path(token: str) -> bool:
    if not token:
        return False
    expanded = Path(token).expanduser()
    return (
        expanded.is_absolute()
        or token.startswith((".", "~"))
        or "/" in token
        or "\\" in token
        or bool(Path(token).suffix)
        or token in {"Dockerfile", "Makefile", "README", "LICENSE"}
    )


def _blocked_path_reason(path: Path) -> str | None:
    for root in BLOCKED_EXTERNAL_ROOTS:
        try:
            path.relative_to(root)
            return f"path is inside blocked system location '{root}'"
        except ValueError:
            continue

    parts = set(path.parts)
    for segment in BLOCKED_PATH_SEGMENTS:
        if segment in parts:
            return f"path is inside blocked sensitive directory '{segment}'"

    if path.name.startswith(".env"):
        return "environment files are not attached"
    if path.name in SENSITIVE_FILENAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return "sensitive key or credential files are not attached"
    return None


def _read_mention(path: Path) -> tuple[str, dict]:
    metadata = {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "size": None,
        "mtime": None,
        "sha256": None,
        "read_status": "ok",
        "error": None,
    }

    reason = _blocked_path_reason(path)
    if reason:
        metadata["read_status"] = "blocked"
        metadata["error"] = reason
        return f"\n\n--- File: {path} (Error reading: {reason}) ---", metadata
    if not path.exists():
        metadata["read_status"] = "missing"
        metadata["error"] = "file not found"
        return f"\n\n--- File: {path} (Error reading: file not found) ---", metadata
    if not path.is_file():
        metadata["read_status"] = "not_file"
        metadata["error"] = "not a file"
        return f"\n\n--- File: {path} (Error reading: not a file) ---", metadata
    size = path.stat().st_size
    metadata["size"] = size
    metadata["mtime"] = path.stat().st_mtime
    if size > MAX_FILE_MENTION_BYTES:
        metadata["read_status"] = "too_large"
        metadata["error"] = f"file too large: {size} bytes"
        return f"\n\n--- File: {path} (Error reading: file too large: {size} bytes) ---", metadata
    try:
        raw = path.read_bytes()
        metadata["sha256"] = hashlib.sha256(raw).hexdigest()
        content = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        metadata["read_status"] = "decode_error"
        metadata["error"] = f"not UTF-8 text: {e}"
        return f"\n\n--- File: {path} (Error reading: not UTF-8 text: {e}) ---", metadata
    if "\x00" in content:
        metadata["read_status"] = "binary"
        metadata["error"] = "appears to be binary"
        return f"\n\n--- File: {path} (Error reading: appears to be binary) ---", metadata
    return f"\n\n--- File: {path} ---\n{content}", metadata


def parse_file_mentions_with_metadata(message: str) -> tuple[str, list[dict]]:
    """Return the message with file contents appended plus structured attachments."""
    candidates: list[tuple[str, str]] = []

    # Explicit @ mentions. Quoted forms can include spaces; bare forms stop at whitespace.
    mention_pattern = re.compile(r"@(?:(['\"])(.*?)\1|([^\s]+))")
    for match in mention_pattern.finditer(message):
        path_str = match.group(2) if match.group(1) else match.group(3)
        if path_str:
            candidates.append((path_str.strip().rstrip(TRAILING_PUNCTUATION), "at"))

    # Quoted paths without @ are considered only if they point to an existing file.
    quoted_pattern = re.compile(r"(?<!@)(['\"])(.*?)\1")
    for match in quoted_pattern.finditer(message):
        path_str = match.group(2).strip()
        if path_str:
            candidates.append((path_str, "quoted"))

    # Unquoted path-like tokens. Avoid ordinary words even if a same-named file exists.
    for token in re.findall(r"(?<!@)(?:~|\.{1,2}|/)?[^\s'\"<>|]+", message):
        path_str = token.strip().rstrip(TRAILING_PUNCTUATION)
        if _looks_like_path(path_str):
            candidates.append((path_str, "token"))

    if not candidates:
        return message, []

    appended_content = []
    attachments = []
    seen = set()

    for path_str, source in candidates:
        if not path_str:
            continue
        path = _resolve_candidate(path_str)
        if path in seen:
            continue
        if source in {"at", "quoted"} or path.is_file():
            file_block, metadata = _read_mention(path)
            metadata["source"] = source
            metadata["original"] = path_str
            metadata["explicit"] = source in {"at", "quoted"} or Path(path_str).expanduser().is_absolute()
            appended_content.append(file_block)
            attachments.append(metadata)
            seen.add(path)

    if appended_content:
        return message + "".join(appended_content), attachments
    return message, []


def parse_file_mentions(message: str) -> str:
    """
    Find local file mentions in the message, read the files, and append their contents.

    Supports @path, @"path with spaces", @'path with spaces', quoted existing
    paths, and path-like unquoted tokens. Actual edits still happen through the
    backend guarded tool layer; this only gives the model read context.
    """
    parsed, _attachments = parse_file_mentions_with_metadata(message)
    return parsed
