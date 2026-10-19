"""Shared state-machine base for DB-persisted, crash-resumable video pipelines.

ProductionService (Film Crew) and MusicVideoService were cloned 1:1 from the same
proven swarm pattern; this module is now the single source of truth for that
lifecycle plumbing. Each pipeline subclasses ``PipelineService`` and supplies four
class attributes:

  - ``model_cls``          the SQLAlchemy row model (Production, MusicVideo, …)
  - ``valid_transitions``  current_stage → next_stage
  - ``stage_to_agent``     current_stage → resuming agent (None = user-gated)
  - ``task_namespace``     celery task prefix ("production", "music_video", …)

and ``stage_context``, the key its stages have in plugin_bridge's
STAGE_PLUGIN_REQUIREMENTS and the orchestrator's STAGE_MODEL_REQUIREMENTS
("film-crew", "music-video"). It is not the task prefix: those maps use the
page names.

The per-pipeline ``create()`` and any domain helpers (cut planning, etc.) stay on
the subclass. Everything here is genre-agnostic — it only touches the lifecycle
spine (status / current_stage / error_blob) that every video kind shares.

Why the atomic UPDATE-WHERE in ``advance_if_predecessor`` matters: two Celery
workers can race under crash-resume + double-dispatch. The atomic update makes
only one win — the loser's update affects zero rows and returns False. Do NOT
simplify it to a read-then-write.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# Raw terminal statuses. Per-stage failures use the ``failed_<stage>`` pattern,
# matched separately in find_non_terminal so failed rows aren't re-dispatched.
TERMINAL_STATUSES = {"complete", "failed"}

# Stage prep at dispatch (start the stage's plugins, run the orchestrator's stage
# model prep) looked the stages up under the celery prefix, which the stage maps
# do not use, so it never did anything. Keyed correctly it starts plugins and,
# for the render stages, evicts every non-video model this process has booked,
# before the render has claimed the GPU. Off until that is wanted.
STAGE_PREP_ENV = "GUAARDVARK_PIPELINE_STAGE_PREP"


def stage_prep_enabled() -> bool:
    return os.environ.get(STAGE_PREP_ENV, "0").strip().lower() in ("1", "true", "yes", "on")


def dispatch_report(warning: str | None) -> dict:
    """Response fields for a route that tried to dispatch an agent:
    ``dispatched``, and ``warning`` (from try_dispatch) when it was not."""
    if warning:
        return {"dispatched": False, "warning": warning}
    return {"dispatched": True}


def _coerce_error(error):
    """Make ``error`` safe for the SQLAlchemy JSON column.

    JSON-native types pass through; Exceptions and anything else get stringified —
    better to lose structure than lose the whole row to a serialization crash on
    commit (which would strand it in its non-terminal stage forever).
    """
    if error is None or isinstance(error, (str, int, float, bool, list, dict)):
        return error
    return str(error)


class PipelineService:
    """Coordinates state transitions on a pipeline row model.

    Take a SQLAlchemy session (Flask-SQLAlchemy's ``db.session`` works). Optionally
    wire a ``gate`` (JobOperationGate) for GPU exclusivity on generation-heavy
    stages. Subclasses set the four class attributes below.
    """

    #: Subclasses MUST override these four.
    model_cls = None
    valid_transitions: dict[str, str] = {}
    stage_to_agent: dict[str, "str | None"] = {}
    task_namespace: str = ""
    stage_context: str = ""
    #: Opt-in: write stage start/end times into settings_json["stage_timings"]
    #: (the music-video progress meter reads them). Off by default so kinds whose
    #: settings_json is user-facing verbatim keep it unchanged.
    record_stage_timings: bool = False

    def __init__(self, session: Session, gate=None):
        self.s = session
        self.gate = gate

    # --- State machine -----------------------------------------------------

    def advance_if_predecessor(self, row_id: int, *, expected_predecessor: str) -> bool:
        """Atomic stage advance. Returns True iff the transition happened.

        The atomic UPDATE-WHERE (current_stage == expected_predecessor) makes only
        one of two racing workers win. Status mirrors current_stage so Activity/UI
        filters see real state.
        """
        next_stage = self.valid_transitions.get(expected_predecessor)
        if next_stage is None:
            return False
        rows = (
            self.s.query(self.model_cls)
            .filter(
                self.model_cls.id == row_id,
                self.model_cls.current_stage == expected_predecessor,
            )
            .update(
                {"current_stage": next_stage, "status": next_stage},
                synchronize_session=False,
            )
        )
        self.s.commit()
        if rows > 0:
            self.stamp_stage(row_id, next_stage, ended=expected_predecessor)
        return rows > 0

    def stamp_stage(self, row_id: int, started: str | None, *, ended: str | None = None,
                    failed: str | None = None) -> None:
        """Record stage boundaries in ``settings_json["stage_timings"]`` for the
        progress meter: ``{stage: {started_at, ended_at, failed_at}}``.

        Runs AFTER the atomic transition has committed, as a separate best-effort
        write: a failure here is logged and never undoes or blocks a transition.
        The row's settings are re-read first so a concurrent settings write is
        not clobbered with a stale copy.
        """
        if not self.record_stage_timings:
            return
        try:
            row = self.s.get(self.model_cls, row_id)
            if row is None:
                return
            self.s.refresh(row, ["settings_json"])
            settings = dict(row.settings_json or {})
            timings = {k: dict(v) for k, v in (settings.get("stage_timings") or {}).items()
                       if isinstance(v, dict)}
            now = datetime.now().isoformat(timespec="seconds")
            if ended:
                timings.setdefault(ended, {})["ended_at"] = now
            if failed:
                timings.setdefault(failed, {})["failed_at"] = now
            if started:
                # A re-entered stage (revise, re-analyze) starts a fresh timing.
                timings[started] = {"started_at": now}
            settings["stage_timings"] = timings
            # Live step detail belongs to the stage that just ended.
            if ended:
                settings.pop("progress", None)
            row.settings_json = settings
            self.s.commit()
        except Exception as e:  # noqa: BLE001 — timing is display-only
            log.warning("stamp_stage failed for %s %s: %s", self.task_namespace, row_id, e)
            try:
                self.s.rollback()
            except Exception:  # noqa: BLE001
                pass

    def fail_stage(self, row_id: int, *, stage: str, error) -> None:
        """Persist a ``failed_<stage>`` status + error blob so the boot resumer skips it."""
        row = self.s.get(self.model_cls, row_id)
        if row is None:
            return
        row.status = f"failed_{stage}"
        row.error_blob = {"stage": stage, "error": _coerce_error(error)}
        self.s.commit()
        self.stamp_stage(row_id, None, failed=stage)

    # --- Resumability ------------------------------------------------------

    def find_non_terminal(self) -> list:
        # Exclude raw terminal statuses AND the per-stage failure pattern, else
        # failed rows get re-dispatched every boot.
        # Also treat explicit "cancelled" (from user cancel on music-video etc.)
        # as terminal so resume_all and dispatch loops don't keep poking them.
        return (
            self.s.query(self.model_cls)
            .filter(
                ~self.model_cls.status.in_(list(TERMINAL_STATUSES)),
                ~self.model_cls.status.like("failed_%"),
                ~self.model_cls.status.like("cancelled%"),
                self.model_cls.status != "cancelled",
            )
            .all()
        )

    def prepare_stage(self, row) -> None:
        """Start the plugins and run the model prep the row's current stage needs.

        Skipped unless GUAARDVARK_PIPELINE_STAGE_PREP is on and the plugin
        auto-orchestrator is enabled. ``ensure_plugins_for_stage`` also runs the
        orchestrator's ``prepare_for_stage``, so this is the only call. A failure
        is logged and the agent is dispatched anyway.
        """
        if not stage_prep_enabled():
            return
        from backend.services.plugin_bridge import (
            auto_orchestrator_enabled,
            ensure_plugins_for_stage,
        )
        if not auto_orchestrator_enabled():
            return
        context = self.stage_context or self.task_namespace
        try:
            ensure_plugins_for_stage(context, row.current_stage)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "Stage prep failed for %s %s stage=%s (non-fatal): %s",
                context, row.id, row.current_stage, e,
            )

    def dispatch_agent(self, row_id: int, agent_name: str) -> None:
        row = self.s.get(self.model_cls, row_id)
        if row:
            self.prepare_stage(row)
        from backend.celery_app import celery
        celery.send_task(f"{self.task_namespace}.run_{agent_name}", args=[row_id])

    def try_dispatch(self, row_id: int, agent_name: str) -> str | None:
        """dispatch_agent for a route that has already moved the row forward:
        None when the agent was queued, otherwise why it was not, worded for
        the response. The row keeps its stage either way; resume_all
        dispatches it when Guaardvark next starts."""
        from backend.celery_dispatch import TaskNotStarted

        try:
            self.dispatch_agent(row_id, agent_name)
        except Exception as e:  # noqa: BLE001 - reported to the caller, not raised
            log.warning("%s %s: %s dispatch failed: %s", self.task_namespace, row_id, agent_name, e)
            why = e.why if isinstance(e, TaskNotStarted) else (str(e) or type(e).__name__)
            step = agent_name.replace("_", " ")
            return (
                f"The {step} was not started: {why}. Nothing is lost: it starts when "
                "Guaardvark is restarted (./start.sh)."
            )
        return None

    def resume_all(self) -> int:
        """Boot-time resume. Dispatch the agent for each non-terminal row's stage.
        User-gated stages (agent is None) are skipped. Per-row dispatch failures
        are caught and logged so one bad row can't strand the rest. Returns the
        count of successful dispatches.
        """
        count = 0
        for row in self.find_non_terminal():
            agent = self.stage_to_agent.get(row.current_stage)
            if agent is None:
                continue
            try:
                # dispatch_agent runs the stage prep.
                self.dispatch_agent(row.id, agent)
                count += 1
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "Resume failed for %s %s (stage=%s): %s",
                    self.task_namespace, row.id, row.current_stage, e,
                )
        return count

    # --- GPU gate ----------------------------------------------------------

    def gpu_stage(self, op_id: str, fn, *args, kind=None, **kwargs):
        """Wrap a GPU-using stage in the JobOperationGate (if configured).

        The gate ensures GPU-exclusive operations (LoRA training, I2V render,
        storyboard image gen) don't trample each other on the shared GPU. If no
        gate is wired, runs ``fn`` directly. ``op_id`` is the native_id of this
        holder; ``kind`` is the JobKind slot (defaults to VIDEO_RENDER). On
        contention ``gpu_exclusive`` raises GpuBusyError — the caller fails the
        stage cleanly rather than double-loading the GPU.
        """
        if self.gate is None:
            return fn(*args, **kwargs)
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session
        if kind is None:
            kind = JobKind.VIDEO_RENDER
        with gpu_session(kind, op_id, cross_process=True):
            return fn(*args, **kwargs)
