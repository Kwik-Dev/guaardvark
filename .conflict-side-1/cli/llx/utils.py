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


# ── Shared safety + helpers (imported by local_tools.py for consistency) ──
# These are the canonical copies. local_tools re-exports or duplicates a subset
# at import time to keep the CLI package self-contained when backend not present.

def make_unified_diff(original: str, updated: str, path: str) -> str:
    """Return unified diff (used by local edit flows and streaming renderers)."""
    import difflib

    a = (original or "").splitlines(keepends=True)
    b = (updated or "").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="")
    )


# ── Project context helpers (for excellent analysis of dragged/external projects) ──
# Supports GUAARDVARK.md (like CLAUDE.md/GROK.md) + auto exploration for website projects.

PROJECT_MARKERS = [
    "GUAARDVARK.md", ".guaardvark.md",
    ".git", "build.py", "package.json", "index.html", "README.md",
    "tailwind.config.js", "vite.config.js", "webpack.config.js",
]

def find_project_root(start: Path | str = None) -> Path:
    """Walk up from start (or cwd) to find project root using markers."""
    if start is None:
        start = Path.cwd()
    current = Path(start).resolve()
    while True:
        for marker in PROJECT_MARKERS:
            if (current / marker).exists():
                return current
        parent = current.parent
        if parent == current:
            return Path(start).resolve()  # fallback to start
        current = parent

def load_guaardvark_instructions(root: Path) -> str:
    """Load GUAARDVARK.md or .guaardvark.md from project root if present."""
    candidates = ["GUAARDVARK.md", ".guaardvark.md"]
    for name in candidates:
        p = root / name
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                return f"[GUAARDVARK.md from {root}]\n{content}\n---\n"
            except Exception:
                return ""
    return ""

def populate_project_context(memory: dict, root: Path | None = None, max_read: int = 5) -> dict:
    """
    Proactively explore a project directory using local tools.
    Populates memory with project_root, instructions, key_files, summary, css_files, etc.
    Used for excellent 'analyze site' flows.
    """
    from llx.local_tools import list_dir, read_file
    if root is None:
        root = find_project_root()
    root = Path(root).resolve()
    memory["project_root"] = str(root)
    memory["project_name"] = root.name

    instructions = load_guaardvark_instructions(root)
    if instructions:
        memory["guaardvark_instructions"] = instructions

    # Explore structure
    listing = list_dir(str(root), cwd=root, max_entries=100)
    folders = listing.get("folders", [])[:20]
    files = [f["name"] for f in listing.get("files", [])][:30]
    memory["project_listing"] = {"folders": folders, "files": files}

    # Find and read key files (initiative: build.py first, then CSS related)
    key_files = {}
    candidates = ["build.py", "index.html", "main.css", "styles.css", "tailwind.config.js", "package.json"]
    css_files = []
    for f in list_dir(str(root), cwd=root).get("files", []):
        name = f["name"]
        if name.lower().endswith((".css", ".scss", ".less")):
            css_files.append(name)
        for cand in candidates:
            if name == cand and len(key_files) < max_read:
                r = read_file(str(root / name), limit=100, cwd=root)
                if r.get("read_status") == "ok":
                    key_files[name] = r.get("content", "")[:3000]

    memory["key_files"] = key_files
    memory["css_files"] = css_files[:10]
    if "build.py" not in key_files and (root / "build.py").exists():
        r = read_file(str(root / "build.py"), limit=80, cwd=root)
        if r.get("read_status") == "ok":
            key_files["build.py"] = r.get("content", "")[:4000]
            memory["key_files"] = key_files

    # Simple summary
    summary_parts = [f"Project: {root.name} at {root}"]
    if key_files:
        summary_parts.append(f"Key files reviewed: {', '.join(key_files.keys())}")
    if css_files:
        summary_parts.append(f"CSS files found: {len(css_files)}")
    memory["project_summary"] = " | ".join(summary_parts)

    # Set active if build.py present (as in scenario)
    if "build.py" in key_files:
        memory["active_file"] = str(root / "build.py")
        if str(root / "build.py") not in memory.get("recent_files", []):
            memory.setdefault("recent_files", []).insert(0, str(root / "build.py"))

    return memory


def generate_and_write_guaardvark_md(root: Path, force: bool = False, use_backend: bool = True) -> dict:
    """
    Recursively scan the project root and auto-generate a GUAARDVARK.md if missing (or force=True).
    This enables the agent to have project-specific instructions for analysis, coding style,
    build process, CSS guidelines, etc. — similar to CLAUDE.md but tailored for Guaardvark agent.

    Returns info about what was done.
    """
    md_path = root / "GUAARDVARK.md"
    if md_path.exists() and not force:
        return {"status": "exists", "path": str(md_path)}

    from llx.local_tools import list_dir, read_file, run_command
    import os

    # Recursive scan using list_dir (respects skips) + some python walk for structure
    structure = []
    key_files_content = {}
    info = {
        "detected_type": "unknown",
        "has_package_json": False,
        "has_pyproject": False,
        "has_build_py": False,
        "css_files": [],
        "readme_content": "",
    }

    # Use populate for initial data
    temp_mem = {}
    populate_project_context(temp_mem, root, max_read=3)
    key_files_content.update(temp_mem.get("key_files", {}))
    css_files = temp_mem.get("css_files", [])

    # Deeper recursive listing (limited depth to avoid huge projects)
    def scan_dir(current: Path, depth=0, max_depth=3):
        if depth > max_depth:
            return
        try:
            listing = list_dir(str(current), cwd=root, max_entries=50)
            for f in listing.get("files", []):
                name = f["name"]
                full = current / name
                rel = str(full.relative_to(root))
                if name.lower() in ("readme.md", "readme"):
                    try:
                        r = read_file(str(full), limit=30, cwd=root)
                        if r.get("read_status") == "ok":
                            info["readme_content"] = r["content"][:1500]
                    except:
                        pass
                if name == "package.json":
                    info["has_package_json"] = True
                    info["detected_type"] = "web/js"
                if name in ("pyproject.toml", "setup.py", "requirements.txt"):
                    info["has_pyproject"] = True
                    if info["detected_type"] == "unknown":
                        info["detected_type"] = "python"
                if name == "build.py":
                    info["has_build_py"] = True
                    info["detected_type"] = "static-site/python-build"
                if name.lower().endswith((".css", ".scss")):
                    info["css_files"].append(rel)
                structure.append(rel)
            for d in listing.get("folders", []):
                dname = d.rstrip("/")
                if dname.startswith(".") or dname in ("node_modules", "venv", "__pycache__", "dist", "build"):
                    continue
                scan_dir(current / dname, depth + 1, max_depth)
        except:
            pass

    scan_dir(root)

    # Build rich content
    content_lines = [
        f"# GUAARDVARK.md — Project Instructions for {root.name}",
        "",
        "## Project Overview",
        f"Root: {root}",
        f"Detected type: {info['detected_type']}",
        "",
    ]

    if info["readme_content"]:
        content_lines.extend(["## README Excerpt", info["readme_content"], ""])

    content_lines.extend([
        "## Key Files & Architecture",
        "Always start analysis by reading these (in order):",
    ])

    if info["has_build_py"]:
        content_lines.append("- build.py (critical: controls CSS/build pipeline, styles, assets)")
    if info["has_package_json"]:
        content_lines.append("- package.json (dependencies, scripts, build config)")
    if info["css_files"]:
        content_lines.append(f"- CSS files ({len(info['css_files'])} found): prioritize modern practices, variables, responsive, accessibility")
    content_lines.append("- README.md, any docs/ or config files")

    content_lines.extend([
        "",
        "## Analysis & Improvement Guidelines",
        "- When user says 'analyze this project' or drags folder: use local tools + backend code tools (read_code, search_code, list_files).",
        "- Review build process first for websites (how CSS/JS is generated/bundled).",
        "- Suggest concrete, minimal changes. Prefer CSS variables, container queries, mobile-first, no !important.",
        "- Use GUAARDVARK tools: /analyze, /suggest, edit via backend edit_code (with verification).",
        "- Store findings as todos and in long-term memory (save_memory).",
        "- For external projects (not under GUAARDVARK_ROOT): treat root as absolute, support full paths in tools.",
        "",
        "## Coding & Style Rules",
        "- Follow existing patterns in the project.",
        "- Keep changes small and reviewable.",
        "- Update this file when architecture changes.",
        "",
        "## Special Notes",
        "Generated by `/init` or auto during analysis. Edit freely.",
        "",
        f"Scanned on: {__import__('time').strftime('%Y-%m-%d')}",
    ])

    # Add structure summary
    if structure:
        content_lines.extend([
            "",
            "## High-level Structure (top files/dirs)",
            "\n".join(f"- {s}" for s in structure[:30]),
        ])

    full_content = "\n".join(content_lines)

    # Write it (prefer backend for safety if connected, else local)
    wrote_via = "local"
    if use_backend:
        try:
            from llx.client import get_client
            client = get_client()
            # Use direct file write if possible, or code tool
            # For simplicity, use local write here, but in future route through backend file ops
            md_path.write_text(full_content, encoding="utf-8")
            wrote_via = "local (backend available)"
        except:
            md_path.write_text(full_content, encoding="utf-8")
    else:
        md_path.write_text(full_content, encoding="utf-8")

    return {
        "status": "created",
        "path": str(md_path),
        "via": wrote_via,
        "detected_type": info["detected_type"],
        "has_build_py": info["has_build_py"],
        "css_count": len(info["css_files"]),
    }
