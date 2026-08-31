"""Cast Identity Manager — recompose prompts; no BibleDesigner invent."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from backend.models import db, Subject, SubjectSample
from backend.services.cast_identity_manager import (
    recompose_sample_prompt,
    recompose_sheet_prompts,
    subject_is_vision_grounded,
    sync_identity_from_refs,
)


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


def test_recompose_sample_prompt_uses_identity_core_not_invented_hair():
    sample = SimpleNamespace(
        framing="close-up",
        expression="neutral",
        lighting="soft key",
        scene="stadium seats",
        angle="face-forward",
    )
    prompt = recompose_sample_prompt(
        sample,
        trigger="sniffy_mcgee",
        class_token="man",
        identity_marks="shaved head, sunglasses",
        include_bible=False,
        bible="long invented auburn hair, slim build, cartoon bear",
    )
    low = prompt.lower()
    assert "a photo of sniffy_mcgee" in low
    assert "man" in low
    assert "auburn" not in low
    assert "cartoon bear" not in low
    assert "stadium" in low or "face-forward" in low


def test_recompose_sheet_prompts_updates_pending_rows(app):
    with app.app_context():
        s = Subject(
            name="Hero",
            kind="character",
            trigger_word="hero_tok",
            bible="invented blonde curls",
            training_settings_json={
                "bible_vision_grounded": True,
                "class_token": "woman",
                "bible_identity_marks": "short dark hair",
            },
        )
        db.session.add(s)
        db.session.commit()
        row = SubjectSample(
            subject_id=s.id,
            index=0,
            angle="face-forward",
            framing="close-up",
            expression="smile",
            lighting="daylight",
            scene="park",
            image_prompt="hero_tok, blonde curls, invented wardrobe, park",
            status="pending",
            promoted_to_training=False,
        )
        db.session.add(row)
        db.session.commit()

        n = recompose_sheet_prompts(s, include_bible=False)
        assert n == 1
        db.session.refresh(row)
        assert "a photo of hero_tok" in row.image_prompt.lower()
        assert "blonde curls" not in row.image_prompt.lower()
        assert "short dark hair" in row.image_prompt.lower()


def test_subject_is_vision_grounded():
    assert subject_is_vision_grounded(SimpleNamespace(training_settings_json={})) is False
    assert subject_is_vision_grounded(
        SimpleNamespace(training_settings_json={"bible_vision_grounded": True})
    ) is True


def test_sync_identity_from_refs_recomposes_without_bible_designer(app, tmp_path):
    with app.app_context():
        ref = tmp_path / "ref1.png"
        ref.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        s = Subject(
            name="Hero",
            kind="character",
            trigger_word="hero_tok",
            bible="old invented look with green mohawk",
            ref_image_paths=[str(ref)],
            training_settings_json={"bible_manual_override": True},
        )
        db.session.add(s)
        db.session.commit()
        db.session.add(SubjectSample(
            subject_id=s.id,
            index=0,
            angle="face-forward",
            framing="close-up",
            expression="neutral",
            lighting="soft",
            scene="office",
            image_prompt="hero_tok, green mohawk, invented look, office",
            status="pending",
            promoted_to_training=False,
        ))
        db.session.commit()
        sid = s.id

        vision = {
            "ok": True,
            "bible": "a man with short hair and glasses",
            "trigger_word": "hero_tok",
            "tags": ["short hair", "glasses", "man"],
            "marks": "short hair, glasses",
            "captions_refreshed": True,
            "sources_used": [str(ref)],
        }

        with patch(
            "backend.services.plugin_bridge.ensure_plugins_for_stage",
            MagicMock(),
        ), patch(
            "backend.services.character_bible_from_refs.rebuild_bible_from_refs",
            return_value=vision,
        ), patch(
            "backend.services.character_bible_from_refs.persist_bible_on_subject",
        ) as persist:
            def _persist(subject, result, refresh_captions=True):
                subject.bible = result["bible"]
                cfg = dict(subject.training_settings_json or {})
                cfg["bible_vision_grounded"] = True
                cfg["bible_identity_marks"] = result.get("marks") or ""
                cfg["class_token"] = "man"
                subject.training_settings_json = cfg
                db.session.commit()

            persist.side_effect = _persist
            out = sync_identity_from_refs(sid, ensure_ollama=True)

        assert out["ok"] is True
        assert out["samples_updated"] >= 1
        db.session.refresh(s)
        assert s.bible == "a man with short hair and glasses"
        assert not (s.training_settings_json or {}).get("bible_manual_override")
        row = SubjectSample.query.filter_by(subject_id=sid).first()
        assert "green mohawk" not in (row.image_prompt or "").lower()
        assert "a photo of hero_tok" in (row.image_prompt or "").lower()
