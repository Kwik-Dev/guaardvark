"""Knowledge-base reads shared by `search` and `rag`.

Both go through the same tools chat and MCP clients use (search_knowledge_base,
list_documents), so the CLI shows the passages, scores and document counts the
rest of the product sees rather than a separate query path.
"""

from __future__ import annotations

from llx.client import LlxClient, LlxError


def _tool(client: LlxClient, name: str, params: dict) -> dict:
    data = client.execute_tool(name, params)
    result = data.get("result") or {}
    if not data.get("success", True) or not result.get("success", False):
        raise LlxError(result.get("error") or data.get("error") or f"{name} failed", 500)
    return result


def search_passages(client: LlxClient, query: str, top_k: int = 5) -> tuple[list[dict], dict]:
    """Return (passages, trace). Each passage: source, page, score, text."""
    meta = _tool(client, "search_knowledge_base", {"query": query, "top_k": top_k}).get("metadata") or {}
    passages = []
    for r in meta.get("results") or []:
        m = r.get("metadata") or {}
        passages.append({
            "source": m.get("source_filename") or m.get("file_path") or "unknown source",
            "page": m.get("page_label"),
            "score": r.get("score"),
            "text": (r.get("text") or "").strip(),
        })
    # Best match first: the retrieval order can differ from the score shown.
    passages.sort(key=lambda p: p["score"] if isinstance(p["score"], (int, float)) else -1, reverse=True)
    return passages, meta.get("retrieval") or {}


def list_documents(client: LlxClient, limit: int = 200) -> tuple[str, dict]:
    """Return (the tool's text listing, its metadata with `total`)."""
    result = _tool(client, "list_documents", {"limit": limit})
    return result.get("output") or "", result.get("metadata") or {}


MAX_INGEST_FILES = 500


def _ensure_folder(client: LlxClient, name: str, parent_path: str) -> str:
    """Create a library folder (or reuse one with that path) and return its path."""
    path = f"{parent_path}/{name}".lstrip("/") if parent_path else name
    try:
        client.post("/api/files/folder", json={"name": name, "parent_path": parent_path})
    except LlxError as e:
        if e.status_code != 409:
            raise
    return path


def ingest_path(client: LlxClient, target, on_file=None) -> list[dict]:
    """Upload a file, or every visible file under a folder, with indexing on.

    A folder keeps its layout: it becomes a library folder of the same name, with
    its subfolders under it. Hidden files and folders are skipped. Returns one
    entry per upload: {"file", "id"} or {"file", "error"}.
    """
    from pathlib import Path

    root = Path(target).expanduser().resolve()
    if not root.exists():
        raise LlxError(f"No such file or folder: {target}", 404)

    if root.is_file():
        plan = [(root, "")]
    else:
        plan = []
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts) or not path.is_file():
                continue
            plan.append((path, rel.parent.as_posix() if rel.parent.as_posix() != "." else ""))
        if len(plan) > MAX_INGEST_FILES:
            raise LlxError(f"{len(plan)} files under {root.name}; /ingest takes up to "
                           f"{MAX_INGEST_FILES} at a time. Point it at a smaller folder.", 400)

    top = _ensure_folder(client, root.name, "") if root.is_dir() else ""
    made: dict[str, str] = {"": top}   # relative dir inside `root` -> library folder path

    done = []
    for path, rel_dir in plan:
        if rel_dir not in made:
            parent, walked = top, ""
            for part in rel_dir.split("/"):
                walked = f"{walked}/{part}".lstrip("/")
                if walked not in made:
                    made[walked] = _ensure_folder(client, part, parent)
                parent = made[walked]
        folder = made[rel_dir]
        try:
            data = client.upload("/api/files/upload", path, folder_path=folder, auto_index="true")
            doc = data.get("data", data)
            entry = {"file": str(path.relative_to(root.parent)), "id": doc.get("id")}
        except LlxError as e:
            entry = {"file": str(path.relative_to(root.parent)), "error": e.message}
        done.append(entry)
        if on_file:
            on_file(entry)
    return done
