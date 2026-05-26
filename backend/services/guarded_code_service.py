"""Guarded source-code operations for self-code agents.

This is the single apply boundary for edits to Guaardvark's own checkout.
Callers may read through it freely, but writes are exact-match, allowlisted,
lock-aware, protected-file-aware, and backed by a diff plus backup.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXCLUDED_SEGMENTS = {
    ".git",
    ".pytest_cache",
    ".swarm-worktrees",
    "__pycache__",
    "data/cache",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "venv",
}

DEFAULT_EXTERNAL_BLOCKED_ROOTS = (
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/root",
    "/run",
    "/sys",
)

DEFAULT_EXTERNAL_EXCLUDED_SEGMENTS = {
    ".aws",
    ".docker",
    ".gnupg",
    ".kube",
    ".ssh",
}

DEFAULT_SENSITIVE_FILENAMES = {
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

DEFAULT_SENSITIVE_SUFFIXES = {
    ".key",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
}


class GuardedCodeError(Exception):
    """Raised when a guarded source-code operation is rejected."""

    def __init__(self, message: str, code: str = "GUARDED_CODE_ERROR", status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class GuardedEditResult:
    file_path: str
    relative_path: str
    backup_path: str
    diff: str
    verification: dict


def default_repo_root() -> Path:
    """Return the configured Guaardvark repository root."""
    env_root = os.environ.get("GUAARDVARK_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _normalized_relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_codebase_locked() -> bool:
    """Return True when the global codebase lock is active."""
    root = default_repo_root()
    if (root / "data" / ".codebase_lock").exists():
        return True

    # If in test mode and no application context is pushed, bypass DB to allow unit test isolation
    import os
    if os.environ.get("GUAARDVARK_MODE") == "test":
        from flask import has_app_context
        if not has_app_context():
            return False

    try:
        from backend.models import SystemSetting, db

        setting = db.session.query(SystemSetting).filter_by(key="codebase_locked").first()
        return bool(setting and str(setting.value).lower() == "true")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error querying lock status, falling back to LOCKED for safety: {e}")
        # Default to locked in production for safety; unlocked in testing/sandboxes
        return os.environ.get("GUAARDVARK_MODE") != "test"



def protected_file_reason(relative_path: str) -> str | None:
    """Return a block reason if the path is protected by config."""
    from backend.config import PROTECTED_FILES

    normalized = relative_path.replace("\\", "/").strip("/")
    for protected in PROTECTED_FILES:
        protected_norm = protected.replace("\\", "/").strip("/")
        if normalized == protected_norm or normalized.endswith(f"/{protected_norm}") or protected_norm in normalized:
            return (
                f"'{protected}' is protected by the kill switch architecture "
                "and cannot be modified by autonomous processes."
            )
    return None


def forbidden_path_reason(relative_path: str) -> str | None:
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return "Empty path"
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "Path traversal is not allowed"
    for segment in DEFAULT_EXCLUDED_SEGMENTS:
        segment_parts = segment.split("/")
        if len(segment_parts) == 1 and any(part == segment for part in parts):
            return f"Self-code operations are not allowed inside '{segment}'"
        if len(segment_parts) > 1 and (normalized == segment or normalized.startswith(f"{segment}/")):
            return f"Self-code operations are not allowed inside '{segment}'"
    if parts[-1].startswith(".env"):
        return "Self-code operations are not allowed for environment files"
    return None


def external_path_reason(path: Path) -> str | None:
    """Return a block reason for explicitly referenced files outside the repo."""
    resolved = path.resolve()
    for blocked_root_str in DEFAULT_EXTERNAL_BLOCKED_ROOTS:
        blocked_root = Path(blocked_root_str)
        if _is_under(resolved, blocked_root):
            return f"External file operations are not allowed inside '{blocked_root}'"

    parts = set(resolved.parts)
    for segment in DEFAULT_EXTERNAL_EXCLUDED_SEGMENTS:
        if segment in parts:
            return f"External file operations are not allowed inside '{segment}'"

    name = resolved.name
    suffix = resolved.suffix.lower()
    if name.startswith(".env"):
        return "External file operations are not allowed for environment files"
    if name in DEFAULT_SENSITIVE_FILENAMES or suffix in DEFAULT_SENSITIVE_SUFFIXES:
        return "External file operations are not allowed for sensitive key or credential files"
    return None


def _read_text_file(file_path: Path) -> str:
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise GuardedCodeError(f"File is not valid UTF-8 text: {e}", "FILE_NOT_TEXT", 415)
    if "\x00" in content:
        raise GuardedCodeError("File appears to be binary, not text", "FILE_NOT_TEXT", 415)
    return content


def verify_syntax(file_path: Path, content: str | None = None) -> bool:
    """Check if the content or file has valid syntax."""
    if file_path.suffix == ".py":
        import py_compile
        import tempfile
        try:
            if content is not None:
                # Compile from string content by creating a temp file
                fd, temp_file_name = tempfile.mkstemp(suffix=".py")
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8') as temp_file:
                        temp_file.write(content)
                    py_compile.compile(temp_file_name, doraise=True)
                    return True
                finally:
                    try:
                        os.unlink(temp_file_name)
                    except Exception:
                        pass
            else:
                py_compile.compile(str(file_path), doraise=True)
                return True
        except py_compile.PyCompileError:
            return False
    elif file_path.suffix == ".json":
        import json
        try:
            if content is not None:
                json.loads(content)
            else:
                json.loads(file_path.read_text(encoding="utf-8"))
            return True
        except ValueError:
            return False
    return True


def resolve_repo_path(
    path: str,
    repo_root: str | Path | None = None,
    *,
    allow_external: bool = False,
) -> tuple[Path, str]:
    """Resolve a caller-provided path against the repo root or an allowed external path."""
    if not path or not str(path).strip():
        raise GuardedCodeError("Path is required", "EMPTY_PATH")

    raw_path = str(path).strip()
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    candidate = Path(raw_path).expanduser()
    explicitly_external = candidate.is_absolute()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()

    if _is_under(candidate, root):
        relative_path = _normalized_relative(candidate, root)
        reason = forbidden_path_reason(relative_path)
        if reason:
            raise GuardedCodeError(reason, "FORBIDDEN_PATH", 403)
        return candidate, relative_path

    if not allow_external:
        raise GuardedCodeError("Path is outside the configured repository root", "PATH_OUTSIDE_REPO", 403)

    if not explicitly_external:
        raise GuardedCodeError(
            "External paths must be absolute or start with '~'",
            "EXTERNAL_PATH_NOT_EXPLICIT",
            403,
        )

    reason = external_path_reason(candidate)
    if reason:
        raise GuardedCodeError(reason, "FORBIDDEN_EXTERNAL_PATH", 403)

    return candidate, str(candidate)


def read_repo_file(
    path: str,
    repo_root: str | Path | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    *,
    allow_external: bool = False,
) -> dict:
    file_path, relative_path = resolve_repo_path(path, repo_root, allow_external=allow_external)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)
    size = file_path.stat().st_size
    if size > max_bytes:
        raise GuardedCodeError("File is too large to read through self-code API", "FILE_TOO_LARGE", 413)
    content = _read_text_file(file_path)
    return {
        "path": str(file_path),
        "relative_path": relative_path,
        "content": content,
        "size": size,
        "last_modified": file_path.stat().st_mtime,
        "scope": "repo" if not Path(relative_path).is_absolute() else "external",
    }


def browse_repo_path(path: str = "", repo_root: str | Path | None = None) -> dict:
    root = Path(repo_root).expanduser().resolve() if repo_root else default_repo_root()
    if not path or path == ".":
        directory = root
        relative_path = "."
    else:
        directory, relative_path = resolve_repo_path(path, root)
    if not directory.exists() or not directory.is_dir():
        raise GuardedCodeError("Directory not found", "DIRECTORY_NOT_FOUND", 404)

    folders = []
    files = []
    for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        child_rel = _normalized_relative(child, root)
        if forbidden_path_reason(child_rel):
            continue
        stat = child.stat()
        item_id = f"repo:{child_rel}"
        if child.is_dir():
            folders.append({
                "id": item_id,
                "name": child.name,
                "path": f"/__repo__/{child_rel}",
                "source_type": "live_repo",
                "relative_path": child_rel,
                "is_repository": child_rel == ".",
                "updated_at": None,
                "subfolder_count": 0,
                "document_count": 0,
                "indexed_document_count": 0,
            })
        elif child.is_file():
            files.append({
                "id": item_id,
                "filename": child.name,
                "name": child.name,
                "path": f"/__repo__/{child_rel}",
                "source_type": "live_repo",
                "relative_path": child_rel,
                "type": child.suffix.lstrip(".") or "text",
                "size": stat.st_size,
                "is_code_file": True,
                "index_status": "NOT_INDEXED",
                "uploaded_at": None,
                "updated_at": None,
            })
    return {
        "path": f"/__repo__/{relative_path}" if relative_path != "." else "/__repo__",
        "relative_path": "" if relative_path == "." else relative_path,
        "folders": folders,
        "documents": files,
        "total_folders": len(folders),
        "total_documents": len(files),
        "has_more": False,
    }


def build_unified_diff(relative_path: str, old_text: str, new_text: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=relative_path,
        tofile=relative_path,
    ))


def apply_exact_replacement(
    path: str,
    old_text: str,
    new_text: str,
    *,
    repo_root: str | Path | None = None,
    require_unlocked: bool = True,
    dry_run: bool = False,
    allow_external: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
) -> GuardedEditResult:
    """Apply one exact replacement after all guarded-code checks."""
    if require_unlocked and is_codebase_locked():
        raise GuardedCodeError("Codebase is locked. Unlock it before applying code changes.", "CODEBASE_LOCKED", 423)
    if old_text is None:
        raise GuardedCodeError("old_text is required", "MISSING_OLD_TEXT")
    if new_text is None:
        raise GuardedCodeError("new_text is required", "MISSING_NEW_TEXT")

    file_path, relative_path = resolve_repo_path(path, repo_root, allow_external=allow_external)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)
    if file_path.stat().st_size > max_bytes:
        raise GuardedCodeError("File is too large to edit through self-code API", "FILE_TOO_LARGE", 413)

    if not Path(relative_path).is_absolute():
        protected_reason = protected_file_reason(relative_path)
        if protected_reason:
            raise GuardedCodeError(protected_reason, "PROTECTED_FILE", 403)

    current_content = _read_text_file(file_path)
    occurrence_count = current_content.count(old_text)

    using_normalized = False
    if occurrence_count == 0:
        # Fallback to normalized line ending search
        normalized_content = current_content.replace("\r\n", "\n")
        normalized_old = old_text.replace("\r\n", "\n")
        occurrence_count = normalized_content.count(normalized_old)
        if occurrence_count == 1:
            using_normalized = True
            current_content = normalized_content
            old_text = normalized_old
            new_text = new_text.replace("\r\n", "\n")

    if occurrence_count == 0:
        raise GuardedCodeError("Exact text was not found; edit was not applied.", "TEXT_NOT_FOUND", 409)
    if occurrence_count > 1:
        raise GuardedCodeError("Exact text is not unique; edit was not applied.", "TEXT_NOT_UNIQUE", 409)

    updated_content = current_content.replace(old_text, new_text)
    diff = build_unified_diff(relative_path, old_text, new_text)

    if dry_run:
        # Dry run syntax check
        if not verify_syntax(file_path, updated_content):
            raise GuardedCodeError("Edit would introduce syntax errors.", "SYNTAX_CHECK_FAILED", 400)

        return GuardedEditResult(
            file_path=str(file_path),
            relative_path=relative_path,
            backup_path="",
            diff=diff,
            verification={
                "command": "exact_text_replacement",
                "return_code": 0,
                "output_summary": "Dry run succeeded: content matched and verified syntax.",
            },
        )

    backup_path = file_path.with_suffix(file_path.suffix + ".backup")
    backup_path.write_text(current_content, encoding="utf-8")

    if backup_path.stat().st_size != len(current_content.encode("utf-8")):
        raise GuardedCodeError("Backup verification failed; edit was not applied.", "BACKUP_FAILED", 500)

    try:
        file_path.write_text(updated_content, encoding="utf-8")
    except Exception as e:
        try:
            file_path.write_text(current_content, encoding="utf-8")
        except Exception:
            pass
        raise GuardedCodeError(f"Write failed: {e}", "WRITE_FAILED", 500)

    # Validate syntax on the newly written file
    if not verify_syntax(file_path):
        try:
            file_path.write_text(current_content, encoding="utf-8")
        except Exception:
            pass
        raise GuardedCodeError("Edit introduced syntax errors; edit was rolled back.", "SYNTAX_CHECK_FAILED", 400)

    try:
        verify_content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        try:
            file_path.write_text(current_content, encoding="utf-8")
        except Exception:
            pass
        raise GuardedCodeError(f"Post-write verify read failed: {e}", "VERIFY_READ_FAILED", 500)

    if verify_content != updated_content:
        try:
            file_path.write_text(current_content, encoding="utf-8")
        except Exception:
            pass
        raise GuardedCodeError("Post-write verification failed; edit was rolled back.", "VERIFY_FAILED", 500)

    return GuardedEditResult(
        file_path=str(file_path),
        relative_path=relative_path,
        backup_path=str(backup_path),
        diff=diff,
        verification={
            "command": "exact_text_replacement",
            "return_code": 0,
            "output_summary": "File content matched expected post-write content and syntax verified.",
        },
    )



def stage_pending_fix(
    path: str,
    old_text: str,
    new_text: str,
    description: str,
    *,
    severity: str = "medium",
    run_id: int | None = None,
    repo_root: str | Path | None = None,
) -> int:
    """Create a PendingFix after validating path/lock/protection, without writing.

    Note: Lock is not enforced on staging since it is purely a proposal and does
    not write to the repo. The lock check is strictly enforced during apply.
    """
    file_path, relative_path = resolve_repo_path(path, repo_root)
    if not file_path.exists() or not file_path.is_file():
        raise GuardedCodeError("File not found", "FILE_NOT_FOUND", 404)

    protected_reason = protected_file_reason(relative_path)
    if protected_reason:
        raise GuardedCodeError(protected_reason, "PROTECTED_FILE", 403)

    current_content = file_path.read_text(encoding="utf-8")
    occurrence_count = current_content.count(old_text)
    if occurrence_count == 0:
        raise GuardedCodeError("Exact text was not found; fix was not staged.", "TEXT_NOT_FOUND", 409)
    if occurrence_count > 1:
        raise GuardedCodeError("Exact text is not unique; fix was not staged.", "TEXT_NOT_UNIQUE", 409)

    from backend.models import PendingFix, db

    pending = PendingFix(
        run_id=run_id,
        file_path=str(file_path),
        original_content=old_text,
        proposed_new_content=new_text,
        proposed_diff=build_unified_diff(relative_path, old_text, new_text),
        fix_description=description,
        severity=severity,
        status="proposed",
    )
    db.session.add(pending)
    db.session.commit()
    return pending.id
