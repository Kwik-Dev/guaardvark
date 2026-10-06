from unittest.mock import patch

import pytest
from flask import Flask

from backend.models import db, Production, ProductionShot
from backend.services.film_curator_service import auto_curate

_FLAG = {"approved": False, "approve": False, "confidence": 20, "reason": "distorted face"}
_PASS = {"approved": True, "approve": True, "confidence": 95, "reason": "clean frame"}


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def production(app):
    prod = Production(name="Curator", script_text="INT. ROOM - DAY", current_stage="awaiting_approval")
    db.session.add(prod)
    db.session.commit()
    return prod


def _shot(prod, number, *, approved=False):
    shot = ProductionShot(production_id=prod.id, scene_number=1, shot_number=number,
                          description=f"Shot {number}", storyboard_image_path=f"/tmp/shot_{number}.png",
                          approved=approved)
    db.session.add(shot)
    db.session.commit()
    return shot


def test_a_hand_approved_shot_judged_flag_stays_approved(app, production):
    by_hand = _shot(production, 1, approved=True)
    waiting = _shot(production, 2)

    with patch("backend.services.film_curator_service.judge_shot", return_value=dict(_FLAG)) as judge:
        summary = auto_curate(production.id)

    db.session.refresh(by_hand)
    db.session.refresh(waiting)
    assert by_hand.approved is True
    assert waiting.approved is False
    assert summary["approved"] == 1
    assert summary["flagged"] == 1
    assert summary["flagged_shots"] == [2]
    judged = [c.args[0].shot_number for c in judge.call_args_list]
    assert judged == [2]


def test_a_rerun_never_clears_an_approval(app, production):
    shot = _shot(production, 1)

    with patch("backend.services.film_curator_service.judge_shot", return_value=dict(_PASS)):
        auto_curate(production.id)
    db.session.refresh(shot)
    assert shot.approved is True

    with patch("backend.services.film_curator_service.judge_shot", return_value=dict(_FLAG)):
        summary = auto_curate(production.id)
    db.session.refresh(shot)
    assert shot.approved is True
    assert summary["flagged"] == 0


def test_a_flagged_shot_stays_waiting_for_the_person(app, production):
    shot = _shot(production, 1)

    with patch("backend.services.film_curator_service.judge_shot", return_value=dict(_FLAG)):
        summary = auto_curate(production.id)

    db.session.refresh(shot)
    db.session.refresh(production)
    assert shot.approved is False
    assert summary["advanced_to_rendering"] is False
    assert production.current_stage == "awaiting_approval"
