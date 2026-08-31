"""Unified cross-kind interface over every video-project kind.

The unification has three legs:
  1. BEHAVIOR  — `PipelineService` (the shared state machine).            [P0.1]
  2. SCHEMA    — `VideoProjectLifecycleMixin` (the shared spine columns).  [P0.2]
  3. INTERFACE — this registry: one place mapping a `kind` string to its   [P0.2]
     (model, service) so generic engine code — a unified "all video projects"
     list, boot-time crash resume across kinds, and the upcoming cross-kind
     Director — runs without `if kind == ...` branches scattered around.

Adapters stay per-kind: Film Crew and Music Video keep their own ``create()`` and
agents; only the lifecycle interface is shared. New output kinds (commercial,
infographic, …) register by adding one entry to ``VIDEO_KINDS``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.models import MusicVideo, Production
from backend.services.music_video_service import MusicVideoService
from backend.services.pipeline_service import PipelineService
from backend.services.production_service import ProductionService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoKind:
    key: str
    label: str
    model: type            # a VideoProjectLifecycleMixin subclass
    service: type          # a PipelineService subclass


# The single source of truth for "what video kinds exist." Keyed by the model's
# stable ``KIND`` discriminator so model and registry can never disagree.
VIDEO_KINDS: dict[str, VideoKind] = {
    Production.KIND: VideoKind(Production.KIND, "Film Crew", Production, ProductionService),
    MusicVideo.KIND: VideoKind(MusicVideo.KIND, "Music Video", MusicVideo, MusicVideoService),
}


def kinds() -> list[str]:
    return list(VIDEO_KINDS)


def service_for(kind: str, session, gate=None) -> PipelineService:
    """The PipelineService for a kind, bound to a session (+ optional GPU gate)."""
    return VIDEO_KINDS[kind].service(session, gate=gate)


def model_for(kind: str):
    return VIDEO_KINDS[kind].model


def get(session, kind: str, row_id: int):
    """Fetch one row by (kind, id), or None for an unknown kind / missing row."""
    vk = VIDEO_KINDS.get(kind)
    if vk is None:
        return None
    return session.get(vk.model, row_id)


def _spine_dict(row, kind: str) -> dict:
    return {
        "kind": kind,
        "id": row.id,
        "name": row.name,
        "status": row.status,
        "current_stage": row.current_stage,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_all(session) -> list[dict]:
    """Every video project across all kinds, newest first — the unified spine view.

    Returns normalized lifecycle dicts (kind/id/name/status/stage/timestamps) only;
    kind-specific payload (script, song, cut_plan, clips) is fetched per-kind when a
    caller actually needs it. This is what a single "all your videos" UI reads.
    """
    rows: list[dict] = []
    for key, vk in VIDEO_KINDS.items():
        for row in session.query(vk.model).all():
            rows.append(_spine_dict(row, key))
    rows.sort(key=lambda r: (r["created_at"] or ""), reverse=True)
    return rows


def resume_all_kinds(session, gate=None) -> dict[str, int]:
    """Boot-time crash resume across ALL kinds. Per-kind isolated — one kind's
    failure can't strand another. Returns {kind: resumed_count}. Never raises."""
    out: dict[str, int] = {}
    for key, vk in VIDEO_KINDS.items():
        try:
            out[key] = vk.service(session, gate=gate).resume_all()
        except Exception as e:  # noqa: BLE001
            log.warning("resume_all failed for video kind '%s' (non-critical): %s", key, e)
            out[key] = 0
    return out
