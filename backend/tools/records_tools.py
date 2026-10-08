"""Look up Guaardvark's own records: projects, clients, documents and the rest.

The knowledge-base tools answer "what do my documents say". They cannot answer
"is there a project called X" or "how many clients do we have": project and
client names are metadata the index never embeds, a file is not in the index
until indexing finishes, and the index holds nothing about Cast members,
productions, tasks or notes. This tool reads the same records the Projects,
Clients, Documents, Cast, Film Crew, Music Video, Tasks and Notes pages show,
straight from the database, so something added a moment ago is already there.
"""

import json
import logging
import os
import re
from html import unescape
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic")
VIDEO_TYPES = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v")
AUDIO_TYPES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")
MEDIA_TYPES = IMAGE_TYPES + VIDEO_TYPES + AUDIO_TYPES

KINDS = (
    "all", "projects", "clients", "documents", "images", "videos", "audio", "websites",
    "tasks", "notes", "cast", "productions", "music_videos", "code_repositories",
)

# Words people use for a kind, so "film crew" or "repos" reaches the right one.
_KIND_ALIASES = {
    "project": "projects", "client": "clients", "customer": "clients", "customers": "clients",
    "document": "documents", "docs": "documents", "doc": "documents", "files": "documents",
    "file": "documents", "uploads": "documents", "image": "images", "photos": "images",
    "pictures": "images", "video": "videos", "clips": "videos", "songs": "audio",
    "music": "audio", "website": "websites", "sites": "websites", "task": "tasks",
    "jobs": "tasks", "note": "notes", "sticky_notes": "notes", "characters": "cast",
    "subjects": "cast", "cast_members": "cast", "film_crew": "productions",
    "production": "productions", "films": "productions", "music_video": "music_videos",
    "code": "code_repositories", "repositories": "code_repositories",
    "repos": "code_repositories", "repo": "code_repositories",
}

_STATUS_WORDS = {
    "INDEXED": "indexed",
    "INDEXING": "still indexing",
    "PENDING": "waiting to be indexed",
    "ERROR": "indexing failed",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _norm_kind(kind: Optional[str]) -> Optional[str]:
    k = (kind or "all").strip().lower().replace(" ", "_").replace("-", "_")
    k = _KIND_ALIASES.get(k, k)
    return k if k in KINDS else None


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _day(value) -> str:
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _plain(html: str, limit: int = 80) -> str:
    text = " ".join(unescape(_TAG_RE.sub(" ", html or "")).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ext_filter(model, types: Tuple[str, ...]):
    from sqlalchemy import func, or_
    conditions = [func.lower(model.filename).like(f"%{t}") for t in types]
    return or_(*conditions)


class FindRecordsTool(BaseTool):
    """Read-only lookup over the records Guaardvark's pages show."""

    name = "find_records"
    read_only = True
    idempotent = True
    observation_chars = 4000
    chat_summary = (
        "Look up Guaardvark's own projects, clients, documents and uploads, images, videos, "
        "audio, websites, tasks, notes, Cast, Film Crew productions, music videos and code repos. "
        "Use it first whenever the user names or searches for one: pass name=\"<the name>\". "
        "No arguments = a count of each kind."
    )
    description = (
        "Look up the records Guaardvark keeps, the same ones its Projects, Clients, Documents, "
        "Images, Websites, Tasks, Notes, Cast, Film Crew, Music Video and Code pages show, read live "
        "from the database so something added a moment ago is included. kind picks one type "
        "(projects, clients, documents, images, videos, audio, websites, tasks, notes, cast, "
        "productions, music_videos, code_repositories) or 'all'. name keeps records whose name, "
        "title or filename contains that text, case-insensitive. With kind 'all' and a name, every "
        "kind is searched for it; with kind 'all' and no name, the reply counts each kind. project "
        "limits documents, images, videos, audio, websites, tasks, productions and music videos to "
        "one project, by name or id. Documents report whether they are indexed and searchable yet. "
        "Use the project id with search_knowledge_base to search inside a project's documents. "
        "Read-only."
    )
    parameters = {
        "kind": ToolParameter(
            name="kind", type="string", required=False, default="all",
            description="Which records: " + ", ".join(KINDS) + " (default all).",
        ),
        "name": ToolParameter(
            name="name", type="string", required=False,
            description="Only records whose name, title or filename contains this text, e.g. 'Drug Commercial'.",
        ),
        "project": ToolParameter(
            name="project", type="string", required=False,
            description="A project name or id; limits files, websites, tasks and videos to that project.",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=25, minimum=1, maximum=100,
            description="Most records to list per kind, 1-100 (default 25).",
        ),
    }

    def execute(self, kind: str = None, name: str = None, project: str = None,
                limit: int = None, **_ignored) -> ToolResult:
        from backend.utils.backend_http import is_mcp_transport, run_tool_in_backend

        args = {k: v for k, v in {"kind": kind, "name": name, "project": project,
                                  "limit": limit}.items() if v not in (None, "")}
        if is_mcp_transport(self):
            # The backend runs this same tool, so MCP clients and chat match the same way.
            return run_tool_in_backend(self.name, args)

        resolved = _norm_kind(kind)
        if resolved is None:
            return ToolResult(success=False, error=f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
        try:
            limit = max(1, min(int(limit or 25), 100))
        except (TypeError, ValueError):
            limit = 25
        name = (name or "").strip() or None
        try:
            return self._run(resolved, name, (project or "").strip() or None, limit)
        except Exception as e:
            logger.error("find_records failed: %s", e, exc_info=True)
            return ToolResult(success=False, error=f"Could not read Guaardvark's records: {e}")

    # ------------------------------------------------------------------ dispatch

    def _run(self, kind: str, name: Optional[str], project: Optional[str], limit: int) -> ToolResult:
        project_row, project_note = (None, None)
        if project:
            project_row, project_note = _resolve_project(project)
            if project_row is None:
                return ToolResult(success=True, output=project_note)

        if kind == "all" and not name:
            return ToolResult(success=True, output=_overview(project_row))

        kinds = [k for k in KINDS if k != "all"] if kind == "all" else [kind]
        per_kind = 5 if kind == "all" else limit
        sections, total = [], 0
        for k in kinds:
            lister = _LISTERS[k]
            count, lines = lister(name, project_row, per_kind)
            if kind == "all" and not count:
                continue
            total += count
            sections.append((k, count, lines))

        scope = f" in project '{project_row.name}'" if project_row else ""
        named = f" matching '{name}'" if name else ""
        if kind == "all":
            if not sections:
                checked = ", ".join(k.replace("_", " ") for k in kinds)
                return ToolResult(success=True, output=(
                    f"Nothing in Guaardvark{scope}{named}. Checked: {checked}. "
                    "Files outside Guaardvark can be searched with find_files."
                ))
            out = [f"GUAARDVARK RECORDS{named}{scope} — {total} found"]
            for k, count, lines in sections:
                out.append(f"\n{_title(k)} ({count}):")
                out.extend(lines)
                if count > len(lines):
                    out.append(f"  … {count - len(lines)} more: call again with kind='{k}'")
            return ToolResult(success=True, output="\n".join(out), metadata={"count": total})

        k, count, lines = sections[0]
        if not count:
            hint = ""
            if name:
                others = _elsewhere(name, k)
                if others:
                    hint = f" The name does appear in: {others}. Try kind='all'."
            return ToolResult(success=True, output=f"No {_title(k).lower()}{named}{scope} in Guaardvark.{hint}")
        out = [f"GUAARDVARK {_title(k).upper()}{named}{scope} — {count} found"
               + (f", showing {len(lines)}" if count > len(lines) else "")]
        out.extend(lines)
        if k == "projects":
            out.append("\nTo search a project's documents, pass its id as project_id to search_knowledge_base.")
        if count > len(lines):
            out.append(f"({count - len(lines)} more; narrow with name, or raise limit up to 100)")
        return ToolResult(success=True, output="\n".join(out), metadata={"count": count})


def _title(kind: str) -> str:
    return {"music_videos": "Music videos", "code_repositories": "Code repositories",
            "cast": "Cast members", "productions": "Film Crew productions"}.get(kind, kind.capitalize())


def _resolve_project(text: str):
    from sqlalchemy import func
    from backend.models import Project, db
    if text.isdigit():
        row = db.session.get(Project, int(text))
        return (row, None) if row else (None, f"No project with id {text} in Guaardvark.")
    exact = Project.query.filter(func.lower(Project.name) == text.lower()).first()
    if exact:
        return exact, None
    rows = Project.query.filter(Project.name.ilike(_like(text))).order_by(Project.name).limit(6).all()
    if len(rows) == 1:
        return rows[0], None
    if rows:
        names = "; ".join(f"{r.name} (id {r.id})" for r in rows)
        return None, f"More than one project matches '{text}': {names}. Name one, or pass its id."
    return None, f"No project named like '{text}' in Guaardvark. find_records(kind='projects') lists them."


# ------------------------------------------------------------------- listers
# Each returns (total matching, lines for at most `limit` of them).

def _projects(name, project_row, limit):
    from backend.models import Project
    q = Project.query
    if name:
        q = q.filter(Project.name.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Project.id == project_row.id)
    total = q.count()
    lines = []
    for p in q.order_by(Project.updated_at.desc().nullslast()).limit(limit).all():
        client = getattr(p, "client_ref", None)
        bits = [f"id {p.id}"]
        if client is not None:
            bits.append(f"client {client.name}")
        if p.project_type:
            bits.append(p.project_type)
        bits.append(f"{p.documents.count()} documents")
        websites = p.websites.count()
        if websites:
            bits.append(f"{websites} websites")
        if p.created_at:
            bits.append(f"created {_day(p.created_at)}")
        line = f"  - {p.name} · " + " · ".join(bits)
        if p.description:
            line += f" — {_plain(p.description, 100)}"
        lines.append(line)
    return total, lines


def _clients(name, project_row, limit):
    from backend.models import Client
    q = Client.query
    if name:
        q = q.filter(Client.name.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Client.id == project_row.client_id)
    total = q.count()
    lines = []
    for c in q.order_by(Client.name).limit(limit).all():
        bits = [f"id {c.id}", f"{c.projects.count()} projects"]
        for extra in (c.industry, c.location):
            if extra:
                bits.append(extra)
        lines.append(f"  - {c.name} · " + " · ".join(bits))
    return total, lines


def _document_query(name, project_row, types: Optional[Tuple[str, ...]], exclude_media: bool):
    from sqlalchemy import not_
    from backend.models import Document
    q = Document.query
    if name:
        q = q.filter(Document.filename.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Document.project_id == project_row.id)
    if types:
        q = q.filter(_ext_filter(Document, types))
    if exclude_media:
        q = q.filter(not_(_ext_filter(Document, MEDIA_TYPES)))
    return q


def _document_lines(q, limit):
    from backend.models import Document
    lines = []
    for d in q.order_by(Document.uploaded_at.desc().nullslast()).limit(limit).all():
        bits = [f"id {d.id}"]
        if d.project is not None:
            bits.append(f"project {d.project.name}")
        if d.folder is not None and d.folder.path:
            bits.append(f"folder {d.folder.path}")
        status = (d.index_status or "").upper()
        word = _STATUS_WORDS.get(status, status.lower() or "status unknown")
        if status == "ERROR" and d.error_message:
            word += f" ({_plain(d.error_message, 80)})"
        bits.append(word)
        if d.uploaded_at:
            bits.append(f"added {_day(d.uploaded_at)}")
        lines.append(f"  - {d.filename} · " + " · ".join(bits))
    return lines


def _make_doc_lister(types: Optional[Tuple[str, ...]], exclude_media: bool) -> Callable:
    def lister(name, project_row, limit):
        q = _document_query(name, project_row, types, exclude_media)
        return q.count(), _document_lines(q, limit)
    return lister


def _websites(name, project_row, limit):
    from backend.models import Website
    q = Website.query
    if name:
        q = q.filter(Website.url.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Website.project_id == project_row.id)
    total = q.count()
    lines = []
    for w in q.order_by(Website.updated_at.desc().nullslast()).limit(limit).all():
        bits = [f"id {w.id}", w.status or "status unknown"]
        if w.project is not None:
            bits.append(f"project {w.project.name}")
        if w.client_ref is not None:
            bits.append(f"client {w.client_ref.name}")
        if w.last_crawled:
            bits.append(f"crawled {_day(w.last_crawled)}")
        lines.append(f"  - {w.url} · " + " · ".join(bits))
    return total, lines


def _tasks(name, project_row, limit):
    from backend.models import Task
    q = Task.query
    if name:
        q = q.filter(Task.name.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Task.project_id == project_row.id)
    total = q.count()
    lines = []
    for t in q.order_by(Task.created_at.desc().nullslast()).limit(limit).all():
        bits = [f"id {t.id}", t.status or "status unknown"]
        if t.type:
            bits.append(t.type)
        if t.project is not None:
            bits.append(f"project {t.project.name}")
        elif t.client_name:
            bits.append(f"client {t.client_name}")
        if t.due_date:
            bits.append(f"due {_day(t.due_date)}")
        lines.append(f"  - {t.name} · " + " · ".join(bits))
    return total, lines


def _load_notes() -> List[Dict[str, Any]]:
    from backend import config
    path = os.path.join(config.STORAGE_DIR, "sticky_notes_state.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f) or {}
    notes = []
    for closed, bucket in ((False, state.get("notes")), (True, state.get("closedNotes"))):
        if not isinstance(bucket, dict):
            continue
        for note_id, note in bucket.items():
            if not isinstance(note, dict):
                continue
            title = (note.get("title") or "").strip()
            text = _plain(note.get("content") or "", 2000)
            if not title and not text:
                continue
            notes.append({"id": note_id, "title": title, "text": text, "closed": closed})
    return notes


def _notes(name, project_row, limit):
    if project_row is not None:
        return 0, []  # notes belong to no project
    notes = _load_notes()
    if name:
        needle = name.lower()
        notes = [n for n in notes if needle in n["title"].lower() or needle in n["text"].lower()]
    lines = []
    for n in notes[:limit]:
        label = n["title"] or "(untitled)"
        line = f"  - {label}" + (" · closed" if n["closed"] else "")
        if n["text"]:
            line += f" — {_plain(n['text'], 90)}"
        lines.append(line)
    return len(notes), lines


def _cast(name, project_row, limit):
    from sqlalchemy import or_
    from backend.models import Subject
    if project_row is not None:
        return 0, []  # Cast members are shared across projects
    q = Subject.query
    if name:
        q = q.filter(or_(Subject.name.ilike(_like(name)), Subject.trigger_word.ilike(_like(name))))
    total = q.count()
    lines = []
    for s in q.order_by(Subject.created_at.desc()).limit(limit).all():
        bits = [f"id {s.id}", s.kind or "subject", f"LoRA {s.training_status or 'untrained'}"]
        if s.trigger_word:
            bits.append(f"trigger word '{s.trigger_word}'")
        lines.append(f"  - {s.name} · " + " · ".join(bits))
    return total, lines


def _make_video_project_lister(model_name: str) -> Callable:
    def lister(name, project_row, limit):
        from backend import models
        model = getattr(models, model_name)
        q = model.query
        if name:
            q = q.filter(model.name.ilike(_like(name)))
        if project_row is not None:
            q = q.filter(model.project_id == project_row.id)
        total = q.count()
        lines = []
        for row in q.order_by(model.updated_at.desc()).limit(limit).all():
            bits = [f"id {row.id}", row.status or "draft"]
            if row.current_stage and row.current_stage != row.status:
                bits.append(f"stage {row.current_stage}")
            if row.project is not None:
                bits.append(f"project {row.project.name}")
            bits.append(f"updated {_day(row.updated_at)}")
            lines.append(f"  - {row.name} · " + " · ".join(bits))
        return total, lines
    return lister


def _code_repositories(name, project_row, limit):
    from sqlalchemy import or_
    from sqlalchemy.orm import aliased
    from backend.models import Folder
    parent = aliased(Folder)
    # A marked folder also marks its subfolders; list the top of each tree.
    q = (Folder.query.outerjoin(parent, Folder.parent_id == parent.id)
         .filter(Folder.is_repository.is_(True))
         .filter(or_(parent.id.is_(None), parent.is_repository.is_(False))))
    if name:
        q = q.filter(Folder.name.ilike(_like(name)))
    if project_row is not None:
        q = q.filter(Folder.project_id == project_row.id)
    total = q.count()
    lines = []
    for f in q.order_by(Folder.name).limit(limit).all():
        line = f"  - {f.name} · folder_id {f.id} · {f.path}"
        if f.description:
            line += f" — {_plain(f.description, 90)}"
        lines.append(line)
    return total, lines


_LISTERS: Dict[str, Callable] = {
    "projects": _projects,
    "clients": _clients,
    "documents": _make_doc_lister(None, exclude_media=True),
    "images": _make_doc_lister(IMAGE_TYPES, exclude_media=False),
    "videos": _make_doc_lister(VIDEO_TYPES, exclude_media=False),
    "audio": _make_doc_lister(AUDIO_TYPES, exclude_media=False),
    "websites": _websites,
    "tasks": _tasks,
    "notes": _notes,
    "cast": _cast,
    "productions": _make_video_project_lister("Production"),
    "music_videos": _make_video_project_lister("MusicVideo"),
    "code_repositories": _code_repositories,
}


def _elsewhere(name: str, skip: str) -> str:
    found = []
    for k, lister in _LISTERS.items():
        if k == skip:
            continue
        try:
            count, _ = lister(name, None, 1)
        except Exception:
            continue
        if count:
            found.append(f"{_title(k).lower()} ({count})")
    return ", ".join(found)


def _overview(project_row) -> str:
    from sqlalchemy import func
    from backend.models import Document

    scope = f" for project '{project_row.name}' (id {project_row.id})" if project_row else ""
    lines = [f"GUAARDVARK RECORDS{scope} — how many of each:"]
    for k, lister in _LISTERS.items():
        try:
            count, _ = lister(None, project_row, 0)
        except Exception as e:
            lines.append(f"  - {_title(k)}: unavailable ({e})")
            continue
        if project_row is not None and k in ("projects", "clients", "notes", "cast"):
            continue
        lines.append(f"  - {_title(k)}: {count}")
    q = Document.query
    if project_row is not None:
        q = q.filter(Document.project_id == project_row.id)
    q = q.filter(~_ext_filter(Document, MEDIA_TYPES))
    states = dict(q.with_entities(Document.index_status, func.count()).group_by(Document.index_status).all())
    if states:
        parts = [f"{n} {_STATUS_WORDS.get((s or '').upper(), (s or 'unknown').lower())}"
                 for s, n in sorted(states.items(), key=lambda kv: -kv[1])]
        lines.append("  Documents by index state: " + ", ".join(parts))
    lines.append("Call again with a kind (and name) to list them.")
    return "\n".join(lines)
