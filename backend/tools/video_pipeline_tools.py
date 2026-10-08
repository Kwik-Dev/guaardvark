"""Chat / MCP tools that start a music-video or Film Crew plan.

They create the project and kick analysis / screenwriting. They do not
approve cuts or start a GPU render — that stays a human gate in Studio.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import is_mcp_caller, is_mcp_transport, run_tool_in_backend

logger = logging.getLogger(__name__)

_AUDIO_EXT = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac")
_MUSIC_VIDEO_RE = re.compile(
    r"\b(generate|create|make|produce)\b.{0,40}\bmusic[\s-]?video\b",
    re.IGNORECASE,
)
# A Film Crew request opens the message, as in cli/llx/intent_router.py: "run the
# film crew on this: ...", "produce this script: INT. ...". The same words later in a
# sentence ("can you produce a python script that ...", "I want to film a script
# reading") are not a request.
_FILM_CREW_RE = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:(?P<crew>(?:start|run|create|make)\s+(?:the\s+|a\s+)?film[\s-]?crew)"
    r"|(?:film|produce)\s+(?:this\s+)?script)\b",
    re.IGNORECASE,
)
# What may sit between the command and the script: "on this:", "with", ":".
_FILM_CREW_LEAD_RE = re.compile(
    r"^\s*(?:(?:on|for|with)(?:\s+(?:this|it|the\s+following))?\b)?\s*:?\s*", re.IGNORECASE,
)
_SCRIPT_LEAD_RE = re.compile(r"^\s*(?:with\b)?\s*:?\s*")


def wants_music_video(message: str) -> bool:
    return bool(message and _MUSIC_VIDEO_RE.search(message))


def wants_film_crew(message: str) -> bool:
    return bool(message and _FILM_CREW_RE.search(message))


_SLASH_MUSIC_VIDEO_RE = re.compile(r"^\s*/music-video\b", re.IGNORECASE)
_SLASH_FILM_CREW_RE = re.compile(r"^\s*/film-crew\b", re.IGNORECASE)


def is_music_video_request(message: str) -> bool:
    return bool(_SLASH_MUSIC_VIDEO_RE.match(message or "") or wants_music_video(message))


def is_film_crew_request(message: str) -> bool:
    return bool(_SLASH_FILM_CREW_RE.match(message or "") or wants_film_crew(message))


def parse_music_video_nl(message: str) -> dict:
    """Pull a song path/id and a style prompt out of a chat line. Pure."""
    text = (message or "").strip()
    text = re.sub(r"^\s*/music-video\b[:\s]*", "", text, flags=re.I)
    text = _MUSIC_VIDEO_RE.sub("", text)
    song = None
    for token in text.split():
        cleaned = token.strip(".,;:\"'")
        if cleaned.isdigit():
            song = cleaned
            break
        if cleaned.lower().endswith(_AUDIO_EXT) or "/" in cleaned:
            song = cleaned
            break
    style = text
    if song:
        style = style.replace(song, " ")
    style = re.sub(
        r"\b(for|from|of|using|with|to)\s+(this\s+)?(song|track|audio)\b[:\s]*",
        " ",
        style,
        flags=re.I,
    )
    style = " ".join(style.split()).strip(" ,.-")
    return {"song": song, "style_prompt": style or None}


def parse_film_crew_nl(message: str) -> dict:
    """Pull a script body (or path) out of a chat line. Pure."""
    text = (message or "").strip()
    text = re.sub(r"^\s*/film-crew\b[:\s]*", "", text, flags=re.I)
    command = _FILM_CREW_RE.match(text)
    if command:
        lead = _FILM_CREW_LEAD_RE if command.group("crew") else _SCRIPT_LEAD_RE
        text = lead.sub("", text[command.end():], count=1)
    else:
        text = _SCRIPT_LEAD_RE.sub("", text, count=1)
    script = " ".join(text.split()).strip()
    return {"script_text": script or None}


# A feature-length screenplay is a few hundred KB of plain text. The cap keeps a
# request thread from reading a model weight or disk image named by mistake.
SCRIPT_FILE_MAX_BYTES = 1024 * 1024

_REFERENCE_PREFIXES = ("http://", "https://", "guaardvark://", "/api/")


def _script_accepted(mcp: bool) -> str:
    where = ("uploads folder or an outputs folder MCP resources serve" if mcp
             else "uploads, outputs or install folder")
    return (
        "Pass the screenplay itself as text, or a UTF-8 text file of up to "
        f"{SCRIPT_FILE_MAX_BYTES // (1024 * 1024)} MB inside Guaardvark's {where} (by path, "
        "/api/outputs/ URL or guaardvark://outputs/ URI)."
    )


def _read_script_file(path: str):
    """(text, None) for a UTF-8 text file up to SCRIPT_FILE_MAX_BYTES, else (None, error)."""
    try:
        size = os.path.getsize(path)
        data = b""
        if size <= SCRIPT_FILE_MAX_BYTES:
            with open(path, "rb") as fh:
                data = fh.read(SCRIPT_FILE_MAX_BYTES + 1)
    except OSError as e:
        return None, f"could not read script file: {e}"
    if size > SCRIPT_FILE_MAX_BYTES or len(data) > SCRIPT_FILE_MAX_BYTES:
        return None, (
            f"script file is {max(size, len(data)) / (1024 * 1024):.1f} MB; script files are read up to "
            f"{SCRIPT_FILE_MAX_BYTES // (1024 * 1024)} MB. Paste the screenplay text instead."
        )
    if b"\x00" in data:
        return None, "script file is not a text file. Save the screenplay as plain UTF-8 text, or paste it."
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        return None, (
            "script file is not UTF-8 text (a .pdf, .docx or other binary file cannot be read as a "
            "script). Save the screenplay as plain UTF-8 text, or paste it."
        )


def _script_body(script_text: str, *, mcp: bool = False):
    """Return (text, None) or (None, error).

    A one-line script_text that names a file (a path, an /api/outputs/ URL or a
    guaardvark://outputs/ URI) is read under the shared media-input rules
    (backend/utils/media_inputs.py) plus a text-only check and a size cap; any
    other text is the script itself.
    """
    from backend.utils.media_inputs import resolve_media_ref

    text = (script_text or "").strip()
    if not text:
        return None, "script_text is required"
    if "\n" in text:
        return text, None
    found = resolve_media_ref(
        text, mcp=mcp, label="script file", within_install=True, accepted=_script_accepted(mcp),
    )
    if found.path:
        return _read_script_file(found.path)
    if found.refused or text.lower().startswith(_REFERENCE_PREFIXES):
        return None, found.error
    return text, None


def _comfyui_note(model_id: str):
    """A line for the result when ``model_id`` renders in ComfyUI and ComfyUI
    is stopped now, else None. Nothing before the clip renders needs it:
    storyboards are drawn offline (character_still_pipeline)."""
    from backend.services.job_types import RenderErrorKind
    from backend.services.plugin_bridge import job_service_start_enabled
    from backend.services.video_model_registry import preflight_video_model

    ready, err = preflight_video_model(model_id)
    if ready or getattr(err, "kind", None) != RenderErrorKind.COMFYUI_DOWN:
        return None
    if job_service_start_enabled():
        return ("ComfyUI is not running now; it is started when the clips render, the last "
                "stage, which you start from Film Crew.")
    return ("ComfyUI is not running now. Nothing needs it until the clips render, the last stage, "
            "which you start from Film Crew: start the ComfyUI plugin (Plugins) before then.")


def _dispatch_first_stage(svc, row_id: int, agent: str):
    """Start a new project's first agent. Returns (started, error text or None).

    The row is created first; when Celery does not take the task (its broker
    is down, say) the row stays at that stage, and a backend restart resumes
    unfinished stages (PipelineService.resume_all).
    """
    if not svc.advance_if_predecessor(row_id, expected_predecessor="draft"):
        # Another worker moved the new row first, so the stage is its to run.
        return False, "another worker already moved the project past its first stage"
    from backend.celery_dispatch import TaskNotStarted

    try:
        svc.dispatch_agent(row_id, agent)
    except TaskNotStarted as e:
        logger.warning("%s dispatch failed for %s: %s", agent, row_id, e)
        return False, e.why
    except Exception as e:  # noqa: BLE001 - reported to the caller, not raised
        logger.warning("%s dispatch failed for %s: %s", agent, row_id, e)
        return False, f"the task queue did not take it ({str(e) or type(e).__name__})"
    return True, None


def _looks_like_audio(path: str) -> bool:
    """True when the file starts like one of the formats _AUDIO_EXT names.

    The extension alone is not enough: a file taken as a song lands in the
    library, where the download route serves it.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return (
        (head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or head[:3] == b"ID3"                                  # MP3 with tags
        or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)  # MPEG / ADTS frame
        or head[:4] in (b"fLaC", b"OggS")
        or head[4:8] == b"ftyp"                                # M4A / AAC in MP4
    )


def _same_bytes(a: Path, b: Path) -> bool:
    import hashlib

    if a.stat().st_size != b.stat().st_size:
        return False

    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    return digest(a) == digest(b)


def _library_song(path: Path):
    """(Document, None) for a local song file, stored the way the library
    stores an upload, or (None, error).

    Library rows keep their path relative to UPLOAD_DIR, the form the download
    route, generate_video's reference_audio and the music-video resolvers read.
    A song already in uploads gets that row in place, reusing one that exists
    (and moving a row an earlier version wrote with the absolute path onto the
    relative one). A song elsewhere (an outputs folder, or in chat the install
    folder) is copied into uploads/Audio first, as the Studio's Music Video
    upload puts a song in the library; the same bytes are copied once.
    """
    from backend import config
    from backend.models import Document, db
    from backend.services.output_registration import register_file
    from backend.utils.filename_resolver import resolve_filesystem_filename

    real = Path(os.path.realpath(path))
    if real.suffix.lower() not in _AUDIO_EXT or not _looks_like_audio(str(real)):
        return None, (f"song '{path.name}' is not an audio file; songs are "
                      f"{', '.join(e.lstrip('.') for e in _AUDIO_EXT)}.")
    uploads = Path(os.path.realpath(config.UPLOAD_DIR))
    refs = uploads / "voice_references"
    if real.is_relative_to(refs):
        return None, "a voice reference clip is not a song; pass the song itself."

    if real.is_relative_to(uploads):
        rel = real.relative_to(uploads).as_posix()
        existing = Document.query.filter_by(path=rel).first()
        if existing:
            return existing, None
        legacy = Document.query.filter(Document.path.in_({str(path), str(real)})).first()
        if legacy:
            legacy.path = rel
            db.session.commit()
            return legacy, None
        folder = real.parent.relative_to(uploads).as_posix()
        doc = register_file(os.path.join(config.UPLOAD_DIR, rel), folder_name="" if folder == "." else folder)
    else:
        audio_dir = Path(config.UPLOAD_DIR) / "Audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        target = audio_dir / real.name
        if not (target.is_file() and _same_bytes(real, target)):
            target = audio_dir / resolve_filesystem_filename(audio_dir, real.name)
            shutil.copy2(real, target)
        existing = Document.query.filter_by(path=f"Audio/{target.name}").first()
        if existing:
            return existing, None
        doc = register_file(str(target), folder_name="Audio")
    if doc is None:
        return None, f"song '{path.name}' could not be added to the library."
    return doc, None


def _document_from_song_ref(song: str, *, mcp: bool = False):
    """Return (Document, None) or (None, error).

    A document id (or its /api/files/document/<id>/download link) names an
    existing row; anything else is resolved under the shared media-input rules
    and stored as a library song (_library_song).
    """
    from backend.models import Document, db
    from backend.utils.media_inputs import accepted_forms, document_id_from_ref, resolve_media_ref

    ref = (song or "").strip()
    if not ref:
        return None, "song is required (document id or path to an audio file)"
    doc_id = document_id_from_ref(ref)
    if doc_id is not None:
        doc = db.session.get(Document, doc_id)
        if not doc:
            return None, f"song document {doc_id} not found"
        return doc, None
    found = resolve_media_ref(
        ref, mcp=mcp, label="song file", within_install=True,
        accepted=accepted_forms(mcp=mcp, documents=True, within_install=True),
    )
    if not found.path:
        return None, found.error
    return _library_song(Path(found.path))


class MusicVideoTool(BaseTool):
    """Start a beat-synced music-video plan. Does not approve or render clips."""

    name = "generate_music_video"
    read_only = False
    destructive = False
    description = (
        "Start a music-video project from a song and a visual style. Uploads or "
        "attaches the song, writes unique cut prompts, and stops at the approval "
        "gate — it does not spend GPU rendering clips. Use when the user asks to "
        "make a music video. Pass song as a document id (generate_music reports one) "
        "or the location of an audio file. For a story from a screenplay, use start_film_crew."
    )
    parameters = {
        "song": ToolParameter(
            name="song",
            type="string",
            description=(
                "The song (mp3/wav/flac/ogg/m4a/aac): a library document id or "
                "/api/files/document/<id>/download link, an /api/outputs/<path> URL, a "
                "guaardvark://outputs/<path> resource URI, or a file path. Over MCP the file must be "
                "in Guaardvark's uploads folder or in an outputs folder MCP resources serve (what "
                "resources/list shows); in chat, anywhere in its uploads, outputs or install folder. "
                "Files named like keys or credentials are refused everywhere. A song given by path "
                "joins the library (one outside the uploads folder is copied into its Audio folder)."
            ),
            required=True,
        ),
        "style_prompt": ToolParameter(
            name="style_prompt",
            type="string",
            description="Visual style for the Director (mood, palette, movement).",
            required=True,
        ),
        "name": ToolParameter(
            name="name",
            type="string",
            description="Project name. Defaults to the song filename.",
            required=False,
        ),
        "i2v_model": ToolParameter(
            name="i2v_model",
            type="string",
            description="Optional I2V model id. Default: the active video model.",
            required=False,
        ),
    }

    def execute(self, song: str, style_prompt: str, name: str | None = None,
                i2v_model: str | None = None, **kwargs) -> ToolResult:
        style_prompt = (style_prompt or "").strip()
        if not style_prompt:
            return ToolResult(success=False, error="style_prompt is required")
        if is_mcp_transport(self):
            # Document rows, the Director and Celery dispatch belong to the backend process.
            arguments = {"song": song, "style_prompt": style_prompt, "name": name, "i2v_model": i2v_model}
            return run_tool_in_backend(self.name, {k: v for k, v in arguments.items() if v is not None})
        try:
            from backend.models import db
            from backend.services.music_video_service import MusicVideoService
            from backend.api.music_video_api import _resolve_song

            from backend.utils.media_inputs import check_media_file

            mcp = is_mcp_caller(self)
            doc, err = _document_from_song_ref(song, mcp=mcp)
            if err:
                return ToolResult(success=False, error=err)
            song_path = _resolve_song(doc.id)
            if not song_path:
                return ToolResult(success=False, error=f"song document {doc.id} is not on disk")
            checked = check_media_file(str(song_path), mcp=mcp, label="song", shown=f"document {doc.id}")
            if checked.error:
                return ToolResult(success=False, error=checked.error)

            settings = {}
            if (i2v_model or "").strip():
                settings["i2v_model"] = i2v_model.strip()
            from backend.services.video_model_registry import resolve_active_video_model
            picked, resolve_err = resolve_active_video_model(
                "i2v", settings.get("i2v_model"), surface="music-video",
            )
            if resolve_err:
                return ToolResult(success=False, error=resolve_err)
            settings["i2v_model"] = picked

            title = (name or "").strip() or Path(doc.filename).stem
            svc = MusicVideoService(db.session)
            mv = svc.create(
                name=title,
                song_document_id=doc.id,
                song_path=song_path,
                style_prompt=style_prompt,
                project_id=None,
                settings=settings,
            )
            started, dispatch_err = _dispatch_first_stage(svc, mv.id, "analyzer")
            db.session.refresh(mv)

            studio = f"/music-video"
            if started:
                next_line = "Analysis is running. Approve the cut plan in Studio before any clip renders."
            else:
                next_line = (
                    f"Analysis was not started: {dispatch_err}. The project is saved at stage "
                    f"'{mv.current_stage}'; its analysis starts when the backend restarts, or on "
                    f"POST /api/music-video/{mv.id}/analyze. Do not create it again."
                )
            return ToolResult(
                success=True,
                output="\n".join([
                    f"Music video '{mv.name}' created (id {mv.id}, stage: {mv.current_stage}).",
                    next_line,
                    f"Open Music Video: {studio}",
                ]),
                metadata={
                    "music_video_id": mv.id,
                    "stage": mv.current_stage,
                    "studio_url": studio,
                    "i2v_model": settings.get("i2v_model"),
                    "approved": False,
                    "analysis_started": started,
                    "dispatch_error": dispatch_err,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("generate_music_video failed")
            return ToolResult(success=False, error=str(e))


class FilmCrewTool(BaseTool):
    """Start a Film Crew production from a script. Does not render shots."""

    name = "start_film_crew"
    read_only = False
    destructive = False
    description = (
        "Start a five-role Film Crew production from a screenplay. The screenwriter "
        "begins at once; casting, storyboards and GPU renders wait for you in Studio. "
        "ComfyUI need not be running: only the clip renders at the end use it, and the "
        "answer says when it is stopped. Use when the user asks to film a script or start "
        "the film crew. For visuals cut to a song, use generate_music_video."
    )
    parameters = {
        "script_text": ToolParameter(
            name="script_text",
            type="string",
            description=(
                "Screenplay or scene list as plain text. A single line that names a file (a path, an "
                "/api/outputs/<path> URL or a guaardvark://outputs/<path> URI) is read instead: UTF-8 "
                "text only, up to 1 MB. Over MCP the file must be in Guaardvark's uploads folder or "
                "in an outputs folder MCP resources serve (what resources/list shows); in chat, "
                "anywhere in its uploads, outputs or install folder. Files named like keys or "
                "credentials are refused everywhere."
            ),
            required=True,
        ),
        "name": ToolParameter(
            name="name",
            type="string",
            description="Production name. Defaults to the first line of the script.",
            required=False,
        ),
        "video_model": ToolParameter(
            name="video_model",
            type="string",
            description="Optional I2V / scene model id. Default: the active video model.",
            required=False,
        ),
    }

    def execute(self, script_text: str, name: str | None = None,
                video_model: str | None = None, **kwargs) -> ToolResult:
        if is_mcp_transport(self):
            # The production row, model resolution and screenwriter dispatch belong to the backend process.
            arguments = {"script_text": script_text, "name": name, "video_model": video_model}
            return run_tool_in_backend(self.name, {k: v for k, v in arguments.items() if v is not None})
        script_text, script_err = _script_body(script_text, mcp=is_mcp_caller(self))
        if script_err:
            return ToolResult(success=False, error=script_err)
        try:
            from backend.models import db
            from backend.services.production_service import ProductionService
            from backend.services.video_model_registry import (
                resolve_active_video_model, VIDEO_MODEL_REGISTRY, model_capabilities,
            )

            settings = {}
            explicit = (video_model or "").strip() or None
            if explicit:
                if explicit not in VIDEO_MODEL_REGISTRY or not model_capabilities(explicit):
                    return ToolResult(success=False, error=f"video_model '{explicit}' is not a video model")
            # Only the clip renders, the last stage, run on this model, and
            # they check (and, with job-service start on, start) ComfyUI then;
            # a stopped ComfyUI does not keep the screenwriter from starting.
            picked, resolve_err = resolve_active_video_model(
                "i2v", explicit, surface="film-crew", comfyui_down_ok=True,
            )
            if resolve_err:
                return ToolResult(success=False, error=resolve_err)
            settings["video_model"] = picked
            comfyui_note = _comfyui_note(picked)

            first = next((ln.strip() for ln in script_text.splitlines() if ln.strip()), "Film Crew")
            title = (name or "").strip() or first[:80]
            svc = ProductionService(db.session)
            prod = svc.create(
                name=title, script_text=script_text, project_id=None, settings=settings,
            )
            started, dispatch_err = _dispatch_first_stage(svc, prod.id, "screenwriter")
            db.session.refresh(prod)

            studio = "/film-crew"
            if started:
                lines = ["The screenwriter is running. Casting, storyboards and renders wait in Studio."]
            else:
                lines = [
                    f"The screenwriter was not started: {dispatch_err}. The production is saved at "
                    f"stage '{prod.current_stage}'; use Re-dispatch on the Film Crew page, or restart "
                    "the backend, which resumes it. Do not create it again."
                ]
            if comfyui_note:
                lines.append(comfyui_note)
            return ToolResult(
                success=True,
                output="\n".join([
                    f"Film Crew '{prod.name}' created (id {prod.id}, stage: {prod.current_stage}).",
                    *lines,
                    f"Open Film Crew: {studio}",
                ]),
                metadata={
                    "production_id": prod.id,
                    "stage": prod.current_stage,
                    "studio_url": studio,
                    "video_model": picked,
                    "rendered": False,
                    "screenwriter_started": started,
                    "dispatch_error": dispatch_err,
                    "comfyui_running": comfyui_note is None,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("start_film_crew failed")
            return ToolResult(success=False, error=str(e))
