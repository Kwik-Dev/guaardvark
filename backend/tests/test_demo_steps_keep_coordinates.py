"""Saving a demonstration's steps keeps their click positions.

The Training page's steps editor shows steps as DemoStep.to_dict() gives them,
with the position as ``coordinates: [x, y]``, and PUTs the edited list back.
The route also takes the column names ``coordinates_x`` / ``coordinates_y``.
Runs on in-memory SQLite.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.api.agent_control_api import agent_control_bp
from backend.models import Demonstration, DemoStep, db

URL = "/api/agent-control/learn/demonstrations/{}/steps"


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:")
    db.init_app(app)
    app.register_blueprint(agent_control_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def demo_id(app):
    demo = Demonstration(name="Open settings", description="test")
    db.session.add(demo)
    db.session.flush()
    db.session.add(DemoStep(demonstration_id=demo.id, step_index=0, action_type="click",
                            target_description="gear icon", coordinates_x=1840, coordinates_y=42))
    db.session.add(DemoStep(demonstration_id=demo.id, step_index=1, action_type="type",
                            target_description="search box", text="wifi"))
    db.session.commit()
    return demo.id


def _stored(demo_id):
    db.session.expire_all()
    rows = DemoStep.query.filter_by(demonstration_id=demo_id).order_by(DemoStep.step_index)
    return [(s.action_type, s.coordinates_x, s.coordinates_y) for s in rows]


def test_steps_saved_back_as_the_editor_shows_them_keep_their_clicks(client, demo_id):
    shown = [s.to_dict() for s in db.session.get(Demonstration, demo_id).steps]
    assert shown[0]["coordinates"] == [1840, 42]
    resp = client.put(URL.format(demo_id), json={"steps": shown})
    assert resp.status_code == 200
    assert _stored(demo_id) == [("click", 1840, 42), ("type", None, None)]
    assert resp.get_json()["demonstration"]["steps"][0]["coordinates"] == [1840, 42]


def test_an_edited_position_is_stored(client, demo_id):
    resp = client.put(URL.format(demo_id), json={"steps": [
        {"action_type": "click", "target_description": "gear icon", "coordinates": [1700, 60]},
    ]})
    assert resp.status_code == 200
    assert _stored(demo_id) == [("click", 1700, 60)]


def test_the_column_names_still_work(client, demo_id):
    resp = client.put(URL.format(demo_id), json={"steps": [
        {"action_type": "click", "target_description": "gear icon", "coordinates_x": 5, "coordinates_y": 6},
    ]})
    assert resp.status_code == 200
    assert _stored(demo_id) == [("click", 5, 6)]


def test_null_coordinates_clear_the_position(client, demo_id):
    resp = client.put(URL.format(demo_id), json={"steps": [
        {"action_type": "click", "target_description": "gear icon", "coordinates": None},
    ]})
    assert resp.status_code == 200
    assert _stored(demo_id) == [("click", None, None)]


@pytest.mark.parametrize("bad", [[1700], [1, 2, 3], "1700,60", ["x", "y"]])
def test_malformed_coordinates_are_refused_and_nothing_changes(client, demo_id, bad):
    resp = client.put(URL.format(demo_id), json={"steps": [
        {"action_type": "click", "target_description": "gear icon", "coordinates": bad},
    ]})
    assert resp.status_code == 400
    assert "coordinates" in resp.get_json()["error"]
    assert _stored(demo_id) == [("click", 1840, 42), ("type", None, None)]
