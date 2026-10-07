"""Issue #8 end-to-end: a generation is recorded and its `reproduce` rebuilds it.

The read-only group smokes (`test_groups_e2e.py`) prove the new `jobs` routes answer.
This file goes one step further: it drives a real ``generate`` through the real Flask app
on the scratch database, reads the recorded row back over ``.../generations``, and checks
that ``reproduce`` emits a body equal to what was submitted.

The two generators are faked at the service seam (the sidecar proxy and the ComfyUI
generator), so nothing spends GPU. The database is the e2e scratch Postgres; the tier
refuses to boot without it. The scratch DB is session-scoped and shared, so a cleanup
fixture empties the two record tables around each test — the tier must be re-runnable
without truncating the database by hand.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _clean_records(inprocess_backend):
    from backend.models import AudioGeneration, InfographicGeneration, db

    def wipe():
        with inprocess_backend.app.app_context():
            db.session.query(AudioGeneration).delete()
            db.session.query(InfographicGeneration).delete()
            db.session.commit()

    wipe()
    yield
    wipe()


def _find(rows, **inputs):
    return [r for r in rows if all(r.get("inputs", {}).get(k) == v for k, v in inputs.items())]


def test_audio_generation_is_recorded_and_reproduced(inprocess_backend, cli_runner,
                                                     isolated_home, monkeypatch):
    from backend.api import audio_foundry_api

    submitted = {}

    def fake_generate(path, payload):
        submitted.update(payload)
        return ({"path": "/out/song_e2e.wav", "duration_s": 30.0, "document_id": 1}, 200)

    monkeypatch.setattr(audio_foundry_api, "_proxy_generate", fake_generate)

    result = cli_runner.invoke(app, ["audio", "music", "lo-fi e2e probe",
                                     "--lyrics", "la la", "--seconds", "30", "--json"])
    assert result.exit_code == 0, result.output
    assert inprocess_backend.called("POST", "/api/audio-foundry/generate/music")

    # The record carries exactly the submitted request.
    jobs = cli_runner.invoke(app, ["audio", "jobs", "--json"])
    assert jobs.exit_code == 0, jobs.output
    rows = json.loads(jobs.output)["data"]["generations"]
    matched = _find(rows, style_prompt="lo-fi e2e probe")
    assert len(matched) == 1, rows
    row = matched[0]
    assert row["kind"] == "music" and row["status"] == "completed"
    assert row["inputs"] == submitted
    assert inprocess_backend.called("GET", "/api/audio-foundry/generations")

    # reproduce rebuilds the same body and sends nothing by default.
    rep = cli_runner.invoke(app, ["audio", "reproduce", str(row["id"]), "--json"])
    assert rep.exit_code == 0, rep.output
    payload = json.loads(rep.output)
    assert payload["sent"] is False
    assert payload["body"] == submitted
    assert payload["path"] == "/api/audio-foundry/generate/music"


def test_infographic_generation_is_recorded_and_reproduced(inprocess_backend, cli_runner,
                                                           isolated_home, monkeypatch):
    from backend.services import infographic_generator

    class _Fake:
        def generate(self, spec, seed=None):
            return {"prompt_id": "p-e2e", "filename": "info_e2e.png", "subfolder": "",
                    "image_url": "/api/infographic/view?filename=info_e2e.png",
                    "prompt": "resolved", "width": 1216, "height": 684,
                    "seed": seed if seed is not None else 1, "duration_s": 5.0}

    monkeypatch.setattr(infographic_generator, "get_infographic_generator", lambda: _Fake())

    result = cli_runner.invoke(app, ["infographic", "generate", "--scene", "e2e bees",
                                     "--style", "editorial", "--seed", "42", "--json"])
    assert result.exit_code == 0, result.output
    assert inprocess_backend.called("POST", "/api/infographic/generate")

    jobs = cli_runner.invoke(app, ["infographic", "jobs", "--json"])
    rows = json.loads(jobs.output)["data"]["generations"]
    matched = _find(rows, scene="e2e bees")
    assert len(matched) == 1, rows
    row = matched[0]
    assert row["seed"] == 42 and row["filename"] == "info_e2e.png"

    rep = cli_runner.invoke(app, ["infographic", "reproduce", str(row["id"]), "--json"])
    payload = json.loads(rep.output)
    assert payload["sent"] is False
    assert payload["body"]["scene"] == "e2e bees"
    assert payload["body"]["seed"] == 42
