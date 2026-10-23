"""Production pipeline state machine. DB-persisted, crash-resumable.

Owns the transition graph for a Production through its stages:
  draft → screenwriting → casting → cinematography → storyboard_gen
  → awaiting_approval → rendering → complete

The lifecycle plumbing (atomic advance, fail, resume, GPU gate) lives in
``PipelineService`` — this module only declares Production's stage graph and its
domain ``create()``. ``VALID_TRANSITIONS`` / ``STAGE_TO_AGENT`` / ``TERMINAL_STATUSES``
remain importable here for back-compat.
"""
from __future__ import annotations

from backend.models import Production
from backend.services.pipeline_service import (
    PipelineService,
    TERMINAL_STATUSES,  # noqa: F401  re-exported for back-compat
    _coerce_error,      # noqa: F401  re-exported for back-compat
)


# Frame shape a production may ask for (settings_json["aspect_ratio"]): the
# storyboard still and the clip canvas. These are the music video's still and
# i2v sizes (music_video_tasks.py), which render Wan 2.2 on a 16 GB card.
# No setting means FILM_DEFAULT_ASPECT; "1:1" keeps each model's own canvas.
FILM_FRAME_SHAPES: dict[str, dict[str, tuple[int, int]]] = {
    "16:9": {"still": (1344, 768), "clip": (832, 480)},
    "9:16": {"still": (768, 1344), "clip": (480, 832)},
}
FILM_ASPECT_RATIOS = ("16:9", "9:16", "1:1")
FILM_DEFAULT_ASPECT = "16:9"


def film_frame_shape(settings: dict | None) -> dict[str, tuple[int, int]] | None:
    """The still and clip canvas for a production's settings, or None for 1:1."""
    aspect = (settings or {}).get("aspect_ratio") or FILM_DEFAULT_ASPECT
    return FILM_FRAME_SHAPES.get(str(aspect))


VALID_TRANSITIONS: dict[str, str] = {
    "draft": "screenwriting",
    "screenwriting": "casting",
    "casting": "cinematography",
    "cinematography": "storyboard_gen",
    "storyboard_gen": "awaiting_approval",
    "awaiting_approval": "rendering",
    "rendering": "complete",
}


# Maps a current_stage to the agent that resumes work there. None means
# the stage is user-gated (no auto-resume on boot).
STAGE_TO_AGENT: dict[str, str | None] = {
    "draft": "screenwriter",          # never reached at boot — draft is pre-pipeline
    "screenwriting": "screenwriter",
    "casting": None,                   # user-driven
    "cinematography": "cinematographer",
    "storyboard_gen": "storyboard_artist",
    "awaiting_approval": None,         # user-gated
    "rendering": "editor",
}


class ProductionService(PipelineService):
    """Coordinates state transitions on Production rows.

    Take a SQLAlchemy session in the constructor (Flask-SQLAlchemy's `db.session`
    works). Optionally wire a `gate` (JobOperationGate) for GPU exclusivity on
    generation-heavy stages.
    """

    model_cls = Production
    valid_transitions = VALID_TRANSITIONS
    stage_to_agent = STAGE_TO_AGENT
    task_namespace = "production"
    stage_context = "film-crew"

    # dispatch_agent runs the stage prep for stage_context + current_stage when
    # GUAARDVARK_PIPELINE_STAGE_PREP is on (see STAGE_PLUGIN_REQUIREMENTS).

    def create(self, *, name: str, script_text: str, project_id: int | None,
               settings: dict | None = None) -> Production:
        # settings_json carries per-production choices the agents read later,
        # such as video_model (which family renders the shots).
        p = Production(
            name=name,
            script_text=script_text,
            project_id=project_id,
            status="draft",
            current_stage="draft",
            settings_json=dict(settings or {}),
        )
        self.s.add(p)
        self.s.commit()
        return p
