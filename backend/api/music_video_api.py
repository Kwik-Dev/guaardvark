"""Music-video pipeline REST API. Drive the MusicVideo state machine.

Front door for: create (→ analyze), inspect (stage + cut/clip counts + the GPU
cost estimate shown before approval), and the approval gate that releases the
expensive per-clip generation.
"""
import logging
import os
from pathlib import Path

from flask import Blueprint, request, jsonify

from backend.models import db, MusicVideo, Project, Document
from backend.services.music_video_service import MusicVideoService

bp = Blueprint("music_video_api", __name__, url_prefix="/api/music-video")
log = logging.getLogger(__name__)

# Rough per-clip wall-clock for the approval-gate estimate: FLUX still (~20s) +
# WAN i2v (~45s) + gate cooldown (~8s) + ffmpeg fill (~2s). Display-only.
_SECONDS_PER_CLIP = 75


def _resolve_song(song_document_id) -> str | None:
    """Absolute on-disk path for a song Document id, or None if unresolvable.

    Uploaded Documents store a path RELATIVE TO UPLOAD_DIR (data/uploads), not
    cwd — same as backend/utils/uploaded_file_resolver. Try the upload-relative
    location first, then absolute, then cwd-relative as a fallback.
    """
    if not song_document_id:
        return None
    doc = db.session.get(Document, song_document_id)
    if not doc:
        return None
    path = getattr(doc, "file_path", None) or doc.path or doc.filename
    if not path:
        return None
    from backend.config import UPLOAD_DIR
    p = Path(path)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path(UPLOAD_DIR) / p)
        candidates.append(Path.cwd() / p)
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


def _mv_dict(mv: MusicVideo) -> dict:
    cut_count = len(mv.cut_plan or [])
    clips = mv.clips or []
    clips_done = sum(1 for c in clips if c.get("status") == "done")
    out = {
        "id": mv.id,
        "name": mv.name,
        "status": mv.status,
        "current_stage": mv.current_stage,
        "project_id": mv.project_id,
        "song_document_id": mv.song_document_id,
        "style_prompt": mv.style_prompt,
        "cut_count": cut_count,
        "clip_count": len(clips),
        "clips_done": clips_done,
        "output_document_id": mv.output_document_id,
        "error_blob": mv.error_blob,
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
    }
    # Surface the cost estimate once the plan exists (i.e. at the approval gate).
    if cut_count:
        est = cut_count * _SECONDS_PER_CLIP
        out["estimate"] = {
            "clips_to_generate": cut_count,
            "seconds_per_clip": _SECONDS_PER_CLIP,
            "estimated_seconds": est,
            "estimated_human": f"~{est // 3600}h {(est % 3600) // 60}m" if est >= 3600 else f"~{est // 60}m",
        }
    return out


@bp.post("")
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    song_document_id = body.get("song_document_id")
    style_prompt = (body.get("style_prompt") or "").strip()
    project_id = body.get("project_id")
    settings = body.get("settings") or {}

    if not name or not style_prompt or not song_document_id:
        return jsonify({"error": "name, song_document_id and style_prompt are required"}), 400

    if project_id is not None and db.session.get(Project, project_id) is None:
        return jsonify({"error": f"project_id {project_id} not found"}), 400

    song_path = _resolve_song(song_document_id)
    if not song_path:
        return jsonify({"error": f"song_document_id {song_document_id} not found on disk"}), 400

    svc = MusicVideoService(db.session)
    mv = svc.create(
        name=name, song_document_id=song_document_id, song_path=song_path,
        style_prompt=style_prompt, project_id=project_id, settings=settings,
    )

    # Kick the pipeline: draft → analyzing, then dispatch the analyzer. A dispatch
    # failure is non-fatal — state moved forward so boot resume_all picks it up.
    if svc.advance_if_predecessor(mv.id, expected_predecessor="draft"):
        try:
            svc.dispatch_agent(mv.id, "analyzer")
        except Exception as e:  # noqa: BLE001
            log.warning(f"Analyzer dispatch failed for music_video {mv.id}: {e}")
        db.session.refresh(mv)

    return jsonify(_mv_dict(mv)), 201


@bp.get("")
def list_music_videos():
    rows = MusicVideo.query.order_by(MusicVideo.created_at.desc()).all()
    return jsonify({"music_videos": [_mv_dict(mv) for mv in rows]})


@bp.get("/<int:mv_id>")
def get_music_video(mv_id):
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_mv_dict(mv))


@bp.post("/<int:mv_id>/approve")
def approve(mv_id):
    """Cost gate: release per-clip generation only on explicit operator approval."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"music_video is at stage '{mv.current_stage}', not awaiting_approval"
        }), 409

    svc = MusicVideoService(db.session)
    if svc.advance_if_predecessor(mv_id, expected_predecessor="awaiting_approval"):
        try:
            svc.dispatch_agent(mv_id, "clip_generator")
        except Exception as e:  # noqa: BLE001
            log.warning(f"Clip generator dispatch failed for music_video {mv_id}: {e}")
        db.session.refresh(mv)

    return jsonify(_mv_dict(mv))
