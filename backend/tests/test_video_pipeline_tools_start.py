"""start_film_crew and generate_music_video report what actually started.

A failed analyzer or screenwriter dispatch was logged as a warning while the
tool said "Analysis is running" / "The screenwriter is running". The
services, the database session and the model registry are faked: no Celery,
database or GPU here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tools import video_pipeline_tools as vpt


class _Service:
    """Stands in for ProductionService / MusicVideoService."""

    dispatch_error = None
    dispatched = []

    def __init__(self, session, gate=None):
        pass

    def create(self, name, **kwargs):
        self.row = SimpleNamespace(id=7, name=name, current_stage="draft", kwargs=kwargs)
        return self.row

    def advance_if_predecessor(self, row_id, *, expected_predecessor):
        self.row.current_stage = {"draft": "next"}.get(expected_predecessor)
        return True

    def dispatch_agent(self, row_id, agent):
        if _Service.dispatch_error:
            raise _Service.dispatch_error
        _Service.dispatched.append((row_id, agent))


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    from backend import models
    from backend.services import music_video_service, production_service, video_model_registry

    _Service.dispatch_error, _Service.dispatched = None, []
    monkeypatch.setattr(models, "db", SimpleNamespace(session=SimpleNamespace(refresh=lambda row: None)))
    monkeypatch.setattr(production_service, "ProductionService", _Service)
    monkeypatch.setattr(music_video_service, "MusicVideoService", _Service)
    resolved = []

    def fake_resolve(role, explicit=None, **kwargs):
        resolved.append(kwargs)
        return "wan22-5b", None

    monkeypatch.setattr(video_model_registry, "resolve_active_video_model", fake_resolve)
    monkeypatch.setattr(video_model_registry, "preflight_video_model", lambda model_id: (True, None))
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3\x03\x00\x00\x00audio")
    monkeypatch.setattr(vpt, "_document_from_song_ref",
                        lambda ref, mcp=False: (SimpleNamespace(id=5, filename="song.mp3"), None))
    from backend.api import music_video_api
    monkeypatch.setattr(music_video_api, "_resolve_song", lambda doc_id: str(song))
    return resolved


SCRIPT = "INT. KITCHEN - NIGHT\nA kettle boils."


def test_the_screenwriter_is_reported_running_only_when_it_was_queued(pipeline):
    res = vpt.FilmCrewTool().execute(script_text=SCRIPT)
    assert res.success and "The screenwriter is running" in res.output
    assert res.metadata["screenwriter_started"] is True and _Service.dispatched == [(7, "screenwriter")]


def test_a_failed_screenwriter_dispatch_is_reported(pipeline):
    _Service.dispatch_error = ConnectionError("Error 111 connecting to localhost:6379")
    res = vpt.FilmCrewTool().execute(script_text=SCRIPT)
    assert res.success  # the production exists; a second call would duplicate it
    assert "The screenwriter is running" not in res.output
    assert "was not started" in res.output and "6379" in res.output
    assert "Re-dispatch" in res.output and "Do not create it again" in res.output
    assert res.metadata["screenwriter_started"] is False and res.metadata["dispatch_error"]


def test_a_failed_analyzer_dispatch_is_reported(pipeline):
    _Service.dispatch_error = ConnectionError("broker down")
    res = vpt.MusicVideoTool().execute(song="5", style_prompt="neon rain")
    assert res.success
    assert "Analysis is running" not in res.output
    assert "Analysis was not started" in res.output and "broker down" in res.output
    assert "/api/music-video/7/analyze" in res.output
    assert res.metadata["analysis_started"] is False


def test_a_queued_analyzer_is_reported_running(pipeline):
    res = vpt.MusicVideoTool().execute(song="5", style_prompt="neon rain")
    assert res.success and "Analysis is running" in res.output
    assert res.metadata["analysis_started"] is True and _Service.dispatched == [(7, "analyzer")]


# ---- ComfyUI is needed only when the clips render -----------------------------------------
def test_a_stopped_comfyui_does_not_block_the_screenwriter(pipeline, monkeypatch):
    from backend.services import plugin_bridge, video_model_registry
    from backend.services.job_types import RenderErrorKind, RenderFailure

    monkeypatch.setattr(video_model_registry, "preflight_video_model", lambda model_id: (
        False, RenderFailure(RenderErrorKind.COMFYUI_DOWN, "Wan 2.2 requires ComfyUI.")))
    monkeypatch.setattr(plugin_bridge, "job_service_start_enabled", lambda: False)
    res = vpt.FilmCrewTool().execute(script_text=SCRIPT)
    assert pipeline[-1].get("comfyui_down_ok") is True
    assert res.success and "The screenwriter is running" in res.output
    assert "ComfyUI is not running now" in res.output and "before then" in res.output
    assert res.metadata["comfyui_running"] is False

    monkeypatch.setattr(plugin_bridge, "job_service_start_enabled", lambda: True)
    res = vpt.FilmCrewTool().execute(script_text=SCRIPT)
    assert "it is started when the clips render" in res.output


def test_a_running_comfyui_adds_nothing(pipeline):
    res = vpt.FilmCrewTool().execute(script_text=SCRIPT)
    assert "ComfyUI" not in res.output and res.metadata["comfyui_running"] is True
