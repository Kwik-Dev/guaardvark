"""Video Editor API — proxy blueprint for the video_editor plugin service.

Modeled on audio_foundry_api.py: thin forwarder to the FastAPI service on 8207.
Auto-discovered by backend.utils.blueprint_discovery.

Resolves Document IDs to absolute paths before forwarding — frontend callers
pass `document_id` for audio_path / video_paths and we substitute the on-disk
file path the plugin actually opens.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import requests
from flask import Blueprint, jsonify, request as flask_request

from backend.services.video_editor_projects import ProjectStore

logger = logging.getLogger(__name__)

video_editor_bp = Blueprint("video_editor", __name__, url_prefix="/api/video-editor")

PLUGIN_URL = "http://127.0.0.1:8207"
QUICK_TIMEOUT = 10        # /health, /status, /config, /jobs
ANALYZE_TIMEOUT = 120     # /analyze — synchronous librosa pass; a few seconds for
                          # a typical song, generous for long/large files.
RENDER_TIMEOUT = 1200     # /beat-sync/render returns a job_id immediately,
                          # but a synchronous melt encode can take ~minutes;
                          # keep generous in case render_mp4=true is requested.

# Named-project store. Lives under STORAGE_DIR/video-editor-projects; migrates the
# legacy single-slot session (state_api's video_editor_session.json) on first use.
try:
    from backend.config import STORAGE_DIR
except ImportError:  # pragma: no cover - script-style import fallback
    from config import STORAGE_DIR  # type: ignore

_PROJECTS_DIR = os.path.join(STORAGE_DIR, "video-editor-projects")
_LEGACY_SESSION_FILE = os.path.join(STORAGE_DIR, "video_editor_session.json")
_project_store = ProjectStore(_PROJECTS_DIR)


def _stringify_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        messages = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(part) for part in item.get("loc", []) if part != "body")
                msg = item.get("msg") or item.get("message") or str(item)
                messages.append(f"{loc}: {msg}" if loc else msg)
            else:
                messages.append(str(item))
        return "; ".join(messages)
    if isinstance(detail, dict):
        return detail.get("message") or detail.get("error") or json.dumps(detail, default=str)
    return str(detail)


def _response_body(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError:
        body = {"message": resp.text or resp.reason or "Plugin returned a non-JSON response"}

    if not isinstance(body, dict):
        body = {"data": body}

    if resp.status_code >= 400:
        detail = body.get("detail")
        error = body.get("error")
        message = (
            _stringify_detail(error)
            if error
            else _stringify_detail(detail)
            if detail is not None
            else body.get("message")
            or resp.reason
            or f"Video Editor service returned {resp.status_code}"
        )
        body = {
            **body,
            "error": message,
            "message": body.get("message") or message,
            "status_code": resp.status_code,
        }

    return body


def _proxy_get(path: str, timeout: int = QUICK_TIMEOUT):
    try:
        resp = requests.get(f"{PLUGIN_URL}{path}", timeout=timeout)
        return _response_body(resp), resp.status_code
    except requests.ConnectionError:
        return {"error": "Video Editor service not running"}, 503
    except requests.Timeout:
        return {"error": f"Video Editor request timed out after {timeout}s"}, 504
    except Exception as e:  # noqa: BLE001
        logger.exception("Video Editor GET %s failed", path)
        return {"error": str(e)}, 500


def _proxy_post(path: str, json_data: dict, timeout: int):
    try:
        resp = requests.post(f"{PLUGIN_URL}{path}", json=json_data, timeout=timeout)
        return _response_body(resp), resp.status_code
    except requests.ConnectionError:
        return {"error": "Video Editor service not running"}, 503
    except requests.Timeout:
        return {"error": f"Video Editor request timed out after {timeout}s"}, 504
    except Exception as e:  # noqa: BLE001
        logger.exception("Video Editor POST %s failed", path)
        return {"error": str(e)}, 500


def _resolve_document(doc_id: Any) -> Optional[str]:
    """Resolve a Document row by id to its absolute on-disk path.

    Uses the canonical uploads-aware resolver (which also handles legacy paths
    like plugins/comfyui output) — NOT a bare cwd join. Returns None if the row
    is missing or no candidate path exists on disk.
    """
    if not doc_id:
        return None
    try:
        from backend.models import Document  # local import — avoid cycle on module load
        from backend.services.document_path_resolver import resolve_document_path
    except ImportError:
        return None
    doc = Document.query.get(doc_id)
    if not doc:
        return None
    resolved = resolve_document_path(doc)
    return str(resolved) if resolved else None


def _expand_paths(payload: dict[str, Any]) -> dict[str, Any]:
    """In-place: replace document_id / video_document_ids with absolute file paths.

    Frontend may send either `audio_path` (string) or `audio_document_id` (int).
    Same for the video pool.
    """
    if "audio_document_id" in payload and not payload.get("audio_path"):
        resolved = _resolve_document(payload.pop("audio_document_id"))
        if resolved:
            payload["audio_path"] = resolved

    if "video_document_ids" in payload and not payload.get("video_paths"):
        ids = payload.pop("video_document_ids") or []
        paths = [_resolve_document(d) for d in ids]
        payload["video_paths"] = [p for p in paths if p]

    return payload


# ---------- read-side ---------------------------------------------------------

@video_editor_bp.route("/health", methods=["GET"])
def health():
    body, status = _proxy_get("/health")
    return jsonify(body), status


@video_editor_bp.route("/status", methods=["GET"])
def status():
    body, status_code = _proxy_get("/status")
    return jsonify(body), status_code


@video_editor_bp.route("/config", methods=["GET"])
def config():
    body, status_code = _proxy_get("/config")
    return jsonify(body), status_code


@video_editor_bp.route("/jobs", methods=["GET"])
def list_jobs():
    body, status_code = _proxy_get(f"/jobs?limit={flask_request.args.get('limit', 50)}")
    return jsonify(body), status_code


@video_editor_bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    body, status_code = _proxy_get(f"/jobs/{job_id}")
    return jsonify(body), status_code


# ---------- write-side --------------------------------------------------------

@video_editor_bp.route("/analyze", methods=["POST"])
def analyze_song_route():
    """Analyze a song → tempo + beat_times + energy sections. Resolves a song document id."""
    payload = flask_request.get_json(silent=True) or {}
    if not payload.get("audio_path"):
        doc_id = payload.pop("song_document_id", None) or payload.pop("audio_document_id", None)
        resolved = _resolve_document(doc_id)
        if resolved:
            payload["audio_path"] = resolved
    body, status_code = _proxy_post("/analyze", payload, timeout=ANALYZE_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/beat-sync/render", methods=["POST"])
def beat_sync_render():
    payload = flask_request.get_json(silent=True) or {}
    payload = _expand_paths(payload)
    body, status_code = _proxy_post("/beat-sync/render", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/auto-editor/trim", methods=["POST"])
def auto_editor_trim():
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("input_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["input_path"] = resolved
    body, status_code = _proxy_post("/auto-editor/trim", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/shotcut/compose", methods=["POST"])
def shotcut_compose():
    payload = flask_request.get_json(silent=True) or {}
    body, status_code = _proxy_post("/shotcut/compose", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/shotcut/compose-arrangement", methods=["POST"])
def shotcut_compose_arrangement():
    """Multi-clip render path. Resolves the song document_id if provided."""
    payload = flask_request.get_json(silent=True) or {}
    if "song_document_id" in payload and not payload.get("audio_path"):
        resolved = _resolve_document(payload.pop("song_document_id"))
        if resolved:
            payload["audio_path"] = resolved
    body, status_code = _proxy_post("/shotcut/compose-arrangement", payload, timeout=RENDER_TIMEOUT)
    if isinstance(body, dict):
        # The plugin returns the rendered .mlt path + a `documents` list, but the
        # Video Editor frontend addresses the finished video by the registered
        # Document id (`rendered_mp4_doc_id`). Derive it from the returned docs
        # (pick the one whose filename/path is the rendered .mp4) so the preview
        # and download link resolve.
        if body.get("rendered_mp4") and not body.get("rendered_mp4_doc_id"):
            for doc in body.get("documents") or []:
                fname = str(doc.get("filename") or doc.get("path") or "").lower()
                if fname.endswith(".mp4"):
                    body["rendered_mp4_doc_id"] = doc.get("id")
                    break
    return jsonify(body), status_code


@video_editor_bp.route("/captions/export", methods=["POST"])
def export_captions():
    """Write an arrangement's captions to an SRT file (registered as a Document).

    SRT is human-editable: each caption is a numbered block with a timecode line
    and the text. Body: { arrangement: {clips:[...]}, filename? }
    Returns { path, document_id, captions }.
    """
    payload = flask_request.get_json(silent=True) or {}
    arrangement = payload.get("arrangement") or {}
    clips = arrangement.get("clips") or []
    captions = []
    for c in clips:
        if not c.get("caption"):
            continue
        captions.append({
            "clip_id": c.get("clip_id"),
            "text": c.get("caption"),
            "start": c.get("timeline_start"),
            "end": c.get("timeline_end"),
            "x": c.get("caption_x"),
            "y": c.get("caption_y"),
            "size": c.get("caption_size"),
            "color": c.get("caption_color"),
            "halign": c.get("caption_halign"),
            "valign": c.get("caption_valign"),
        })
    if not captions:
        return jsonify({"error": "No captions in the arrangement to export"}), 400

    from backend.config import UPLOAD_DIR
    from backend.services.output_registration import register_file

    captions_dir = Path(UPLOAD_DIR) / "Captions"
    captions_dir.mkdir(parents=True, exist_ok=True)
    # Take only the basename so a user-supplied name can never point outside the
    # Captions directory (path traversal). ".srt" is appended if not already there.
    name = Path((payload.get("filename") or "captions").strip() or "captions").name
    if not name.endswith(".srt"):
        name += ".srt"
    out_path = captions_dir / name
    out_path.write_text(_captions_to_srt(captions), encoding="utf-8")

    doc = register_file(
        physical_path=str(out_path),
        folder_name="Captions",
        filename=out_path.name,
        file_type=".srt",
        file_metadata={"kind": "video_editor_captions", "count": len(captions)},
    )
    return jsonify({
        "path": str(out_path),
        "document_id": doc.id if doc else None,
        "captions": captions,
    })


@video_editor_bp.route("/captions/import", methods=["POST"])
def import_captions():
    """Read an SRT caption file (by document_id or path) and return its captions.

    Body: { document_id? | path? }
    Returns { captions: [{text, start, end}] }.
    """
    payload = flask_request.get_json(silent=True) or {}
    path = None
    doc_id = payload.get("document_id")
    if doc_id:
        path = _resolve_document(doc_id)
    if not path:
        path = payload.get("path")
    if not path:
        return jsonify({"error": "Provide document_id or path"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": f"Caption file not found: {path}"}), 404
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Could not read caption file: {e}"}), 400
    captions = _parse_srt(text)
    if not captions:
        return jsonify({"error": "No captions parsed from the SRT file"}), 400
    return jsonify({"captions": captions})


def _to_srt_time(seconds: float) -> str:
    """Format seconds as SRT timecode HH:MM:SS,mmm."""
    ms = max(0, int(round(float(seconds) * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _captions_to_srt(captions: list[dict]) -> str:
    """Render captions as an SRT document."""
    lines: list[str] = []
    for i, c in enumerate(captions, 1):
        start = _to_srt_time(c.get("start") or 0)
        end = _to_srt_time(c.get("end") or 0)
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(str(c.get("text") or ""))
        lines.append("")
    return "\n".join(lines)


def _srt_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt(text: str) -> list[dict]:
    """Parse an SRT document into [{text, start, end}] (order preserved)."""
    import re

    captions: list[dict] = []
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        time_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if time_idx is None:
            continue
        m = re.match(
            r"(\d+):(\d+):(\d+)[,.]?(\d*)\s*-->\s*(\d+):(\d+):(\d+)[,.]?(\d*)",
            lines[time_idx],
        )
        if not m:
            continue
        start = _srt_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4) or "0")
        end = _srt_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8) or "0")
        text = " ".join(lines[time_idx + 1:])
        captions.append({"text": text, "start": start, "end": end})
    return captions




@video_editor_bp.route("/catalog/filters", methods=["GET"])
def list_filter_catalog():
    body, status_code = _proxy_get("/catalog/filters")
    return jsonify(body), status_code


@video_editor_bp.route("/catalog/transitions", methods=["GET"])
def list_transition_catalog():
    body, status_code = _proxy_get("/catalog/transitions")
    return jsonify(body), status_code


@video_editor_bp.route("/vision/rescan-clip", methods=["POST"])
def rescan_clip():
    """Force-bust the cache for one clip and re-run vision analysis."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("source_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["source_path"] = resolved
    body, status_code = _proxy_post("/vision/rescan-clip", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/clip-hash", methods=["POST"])
def get_clip_hash():
    """Resolve a clip's content hash for building frame-thumbnail URLs."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_id" in payload and not payload.get("source_path"):
        resolved = _resolve_document(payload.pop("document_id"))
        if resolved:
            payload["source_path"] = resolved
    body, status_code = _proxy_post("/vision/clip-hash", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/frames/<clip_hash>/<int:frame_index>", methods=["GET"])
def get_sampled_frame(clip_hash: str, frame_index: int):
    """Stream a sampled JPEG frame from the plugin to the browser."""
    import requests
    try:
        resp = requests.get(
            f"{PLUGIN_URL}/vision/frames/{clip_hash}/{frame_index}",
            timeout=QUICK_TIMEOUT,
            stream=False,
        )
    except requests.ConnectionError:
        return jsonify({"error": "Video Editor service not running"}), 503

    if resp.status_code != 200:
        return jsonify({"error": f"plugin returned {resp.status_code}"}), resp.status_code
    from flask import Response
    return Response(resp.content, mimetype="image/jpeg")


# ---------- A1 endpoints: bin-driven Plan pipeline ---------------------------

@video_editor_bp.route("/recipes", methods=["GET"])
def list_recipes():
    body, status_code = _proxy_get("/recipes")
    return jsonify(body), status_code


@video_editor_bp.route("/plan", methods=["POST"])
def submit_plan():
    """Bin + song → arrangement. Resolves bin clip document_ids to paths first."""
    payload = flask_request.get_json(silent=True) or {}

    # Expand bin_clips' document_id → source_path
    expanded_bin: list[dict[str, Any]] = []
    unresolved_clip_ids: list[Any] = []
    for entry in payload.get("bin_clips") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("source_path"):
            expanded_bin.append(entry)
            continue
        doc_id = entry.get("document_id")
        if doc_id:
            path = _resolve_document(doc_id)
            if path:
                expanded_bin.append({
                    "clip_id": entry.get("clip_id") or f"doc{doc_id}",
                    "source_path": path,
                    "document_id": doc_id,
                })
            else:
                unresolved_clip_ids.append(doc_id)
    payload["bin_clips"] = expanded_bin

    # Expand song
    unresolved_song_id = None
    if not payload.get("song_path") and payload.get("song_document_id"):
        path = _resolve_document(payload["song_document_id"])
        if path:
            payload["song_path"] = path
        else:
            unresolved_song_id = payload["song_document_id"]

    if unresolved_clip_ids or not expanded_bin:
        return jsonify({
            "error": "Could not resolve one or more video clips for planning.",
            "unresolved_clip_document_ids": unresolved_clip_ids,
            "resolved_clip_count": len(expanded_bin),
        }), 400
    if unresolved_song_id or not payload.get("song_path"):
        return jsonify({
            "error": "Could not resolve the master soundtrack for planning.",
            "unresolved_song_document_id": unresolved_song_id,
        }), 400

    body, status_code = _proxy_post("/plan", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/vision/scan-clips", methods=["POST"])
def vision_scan_clips():
    """A1: returns neutral defaults. A3: real vision call inside the plugin."""
    payload = flask_request.get_json(silent=True) or {}
    if "document_ids" in payload and not payload.get("clip_paths"):
        ids = payload.pop("document_ids") or []
        payload["clip_paths"] = [p for p in (_resolve_document(d) for d in ids) if p]
    body, status_code = _proxy_post("/vision/scan-clips", payload, timeout=RENDER_TIMEOUT)
    return jsonify(body), status_code


@video_editor_bp.route("/open-in-shotcut", methods=["POST"])
def open_in_shotcut():
    payload = flask_request.get_json(silent=True) or {}
    body, status_code = _proxy_post("/open-in-shotcut", payload, timeout=QUICK_TIMEOUT)
    return jsonify(body), status_code


# ---------- Named projects (file-per-project store) --------------------------
# Draft-buffer save model: PUT /projects/current = autosave (writes the draft);
# PUT /projects/<id> = explicit Save (promotes the draft to the project file).
# Card layout stays GLOBAL on /api/state/video-editor — not per project.

def _project_error(e: Exception, what: str):
    if isinstance(e, FileNotFoundError):
        return jsonify({"error": f"{what}: project not found"}), 404
    if isinstance(e, ValueError):  # invalid project id (path-traversal guard)
        return jsonify({"error": f"{what}: {e}"}), 400
    logger.exception("video-editor projects: %s failed", what)
    return jsonify({"error": str(e)}), 500


@video_editor_bp.route("/projects", methods=["GET"])
def list_projects():
    """Metadata-only catalog for the gallery + the current project id."""
    try:
        return jsonify(_project_store.list_projects()), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "list")


@video_editor_bp.route("/projects", methods=["POST"])
def create_project():
    payload = flask_request.get_json(silent=True) or {}
    name = (payload.get("name") or "Untitled").strip() or "Untitled"
    try:
        return jsonify(_project_store.create(name, editable=payload.get("editable"))), 201
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "create")


@video_editor_bp.route("/projects/current", methods=["GET"])
def get_current_project():
    """The project to open on load — migrating the legacy session or creating an
    Untitled if there's nothing yet. Returns working state (draft if newer) + _meta."""
    try:
        return jsonify(_project_store.ensure_current(legacy_path=_LEGACY_SESSION_FILE)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "current")


@video_editor_bp.route("/projects/current", methods=["PUT"])
def autosave_current_project():
    """Autosave → writes the draft shadow of the current project (never the project)."""
    payload = flask_request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "autosave body must be a JSON object"}), 400
    try:
        pid = _project_store.get_current_id()
        if not pid or not _project_store.exists(pid):
            # Establish current FIRST (migrating the legacy session if present), so a
            # debounced autosave racing the initial load can't bypass migration and
            # strand the old single-slot session.
            pid = _project_store.ensure_current(legacy_path=_LEGACY_SESSION_FILE)["_meta"]["id"]
        return jsonify(_project_store.save_draft(pid, payload)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "autosave")


@video_editor_bp.route("/projects/<pid>/draft", methods=["PUT"])
def autosave_project_draft(pid: str):
    """Autosave → writes the draft of a SPECIFIC project id (not the server's
    'current' pointer). The client targets the id it is editing, so an in-flight
    autosave can never land in a different project after the user switches."""
    payload = flask_request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"error": "draft body must be a JSON object"}), 400
    try:
        return jsonify(_project_store.save_draft(pid, payload)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "autosave-draft")


@video_editor_bp.route("/projects/<pid>", methods=["GET"])
def open_project(pid: str):
    try:
        return jsonify(_project_store.open(pid)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "open")


@video_editor_bp.route("/projects/<pid>", methods=["PUT"])
def save_project(pid: str):
    """Explicit Save → promote the draft (or an explicit body) to the project file."""
    payload = flask_request.get_json(silent=True)
    # A dict body merges on top of the newest state; no body (or empty) promotes
    # the draft. A non-dict body is rejected rather than silently mishandled.
    if payload is not None and not isinstance(payload, dict):
        return jsonify({"error": "save body must be a JSON object"}), 400
    editable = payload if payload else None
    try:
        return jsonify(_project_store.save(pid, editable=editable)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "save")


@video_editor_bp.route("/projects/<pid>/save-as", methods=["POST"])
def save_project_as(pid: str):
    payload = flask_request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        return jsonify(_project_store.save_as(pid, name, editable=payload.get("editable"))), 201
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "save-as")


@video_editor_bp.route("/projects/<pid>", methods=["PATCH"])
def rename_project(pid: str):
    payload = flask_request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        return jsonify(_project_store.rename(pid, name)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "rename")


@video_editor_bp.route("/projects/<pid>", methods=["DELETE"])
def delete_project(pid: str):
    try:
        return jsonify(_project_store.delete(pid)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "delete")


@video_editor_bp.route("/projects/<pid>/validate", methods=["POST"])
def validate_project(pid: str):
    """Reference-integrity report: each bin clip classified ok|missing|stale."""
    try:
        return jsonify(_project_store.validate_refs(pid, _resolve_document)), 200
    except Exception as e:  # noqa: BLE001
        return _project_error(e, "validate")
