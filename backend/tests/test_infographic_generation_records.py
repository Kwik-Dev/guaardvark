"""Infographic renders are recorded in the main DB (issue #8).

``/api/infographic/generate`` is synchronous and used to keep nothing, so a finished
infographic could not be found or rebuilt. The backend now stores the spec and the
result and annotates the response with ``generation_id``; ``/generations`` lists them.

Runs on in-memory SQLite with the ComfyUI generator faked: no GPU, no network.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.api.infographic_api import infographic_bp
from backend.models import InfographicGeneration, db


class _FakeGenerator:
    def generate(self, spec, seed=None):
        return {
            "prompt_id": "p1", "filename": "info_1.png", "subfolder": "",
            "image_url": "/api/infographic/view?filename=info_1.png",
            "prompt": "resolved prompt", "width": 1216, "height": 684,
            "seed": seed if seed is not None else 7, "duration_s": 5.4,
        }


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      STORAGE_DIR=str(tmp_path))
    db.init_app(app)
    app.register_blueprint(infographic_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app, monkeypatch):
    from backend.services import infographic_generator

    monkeypatch.setattr(infographic_generator, "get_infographic_generator",
                        lambda: _FakeGenerator())
    return app.test_client()


def test_generate_records_the_spec_and_result(client):
    res = client.post("/api/infographic/generate", json={
        "scene": "five facts about bees", "title": "Bees", "style": "editorial",
        "aspect": "16:9", "hashtags": ["bees"], "callouts": ["one", "two"], "seed": 42,
    })
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True and body["generation_id"] == 1

    row = InfographicGeneration.query.get(1)
    assert row.status == "completed"
    assert row.inputs["scene"] == "five facts about bees"
    assert row.inputs["hashtags"] == ["bees"] and row.inputs["callouts"] == ["one", "two"]
    assert row.seed == 42
    assert row.filename == "info_1.png" and row.width == 1216 and row.height == 684


def test_generate_without_a_scene_is_refused_and_not_recorded(client):
    res = client.post("/api/infographic/generate", json={"title": "empty"})
    assert res.status_code == 400
    assert InfographicGeneration.query.count() == 0


def test_generations_list_and_get(client):
    with client.application.app_context():
        db.session.add(InfographicGeneration(status="completed",
                                             inputs={"scene": "a"}, seed=1, filename="a.png"))
        db.session.add(InfographicGeneration(status="completed",
                                             inputs={"scene": "b"}, seed=2, filename="b.png"))
        db.session.commit()

    listed = client.get("/api/infographic/generations").get_json()["generations"]
    assert [g["inputs"]["scene"] for g in listed] == ["b", "a"]  # newest first

    one = client.get(f"/api/infographic/generations/{listed[0]['id']}").get_json()
    assert one["generation"]["filename"] == "b.png"

    assert client.get("/api/infographic/generations/99999").status_code == 404
