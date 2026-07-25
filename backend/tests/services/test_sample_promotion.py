"""Tests for post-train sample promotion into durable Training Data."""
from __future__ import annotations

import pytest
from flask import Flask

from backend.models import db, Subject, SubjectSample
from backend.services.sample_promotion import promote_samples_after_train


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _subject_with_samples(ref_paths=None):
    s = Subject(
        name="Batman",
        kind="character",
        training_status="trained",
        ref_image_paths=list(ref_paths or ["/refs/a.png", "/refs/b.png"]),
    )
    db.session.add(s)
    db.session.flush()
    samples = []
    for i, path in enumerate(["/gen/s0.png", "/gen/s1.png", "/gen/s2.png"]):
        row = SubjectSample(
            subject_id=s.id,
            index=i,
            status="done",
            approved=True,
            image_path=path,
            angle=f"angle-{i}",
            promoted_to_training=False,
        )
        db.session.add(row)
        samples.append(row)
    # Unapproved leftover — must not promote
    db.session.add(SubjectSample(
        subject_id=s.id, index=99, status="done", approved=False,
        image_path="/gen/reject.png",
    ))
    db.session.commit()
    return s, samples


def test_promote_marks_samples_and_appends_refs(app):
    with app.app_context():
        s, samples = _subject_with_samples()
        used = ["/refs/a.png", "/refs/b.png", "/gen/s0.png", "/gen/s1.png"]
        result = promote_samples_after_train(s, used)
        db.session.commit()

        assert result["promoted"] == 2
        assert set(result["paths_added"]) == {"/gen/s0.png", "/gen/s1.png"}
        s = db.session.get(Subject, s.id)
        assert "/gen/s0.png" in (s.ref_image_paths or [])
        assert "/gen/s1.png" in (s.ref_image_paths or [])
        assert "/gen/s2.png" not in (s.ref_image_paths or [])  # not in used_images

        s0 = db.session.get(SubjectSample, samples[0].id)
        s1 = db.session.get(SubjectSample, samples[1].id)
        s2 = db.session.get(SubjectSample, samples[2].id)
        assert s0.promoted_to_training is True and s0.promoted_at is not None
        assert s1.promoted_to_training is True
        assert s2.promoted_to_training is False


def test_promote_idempotent(app):
    with app.app_context():
        s, _ = _subject_with_samples()
        used = ["/gen/s0.png"]
        promote_samples_after_train(s, used)
        db.session.commit()
        s = db.session.get(Subject, s.id)
        refs_after_first = list(s.ref_image_paths or [])
        result2 = promote_samples_after_train(s, used)
        db.session.commit()
        assert result2["promoted"] == 0
        assert result2["paths_added"] == []
        s = db.session.get(Subject, s.id)
        assert s.ref_image_paths == refs_after_first


def test_promote_empty_used_promotes_all_approved_done(app):
    with app.app_context():
        s, samples = _subject_with_samples()
        result = promote_samples_after_train(s, [])
        db.session.commit()
        assert result["promoted"] == 3
        for smp in samples:
            row = db.session.get(SubjectSample, smp.id)
            assert row.promoted_to_training is True


def test_train_success_real_promotes_via_task(app, tmp_path, monkeypatch):
    """Integration: real-train success path promotes samples into refs."""
    import json
    from pathlib import Path
    from backend.tasks.lora_trainer_tasks import train_subject_lora_for_subject

    with app.app_context():
        s = Subject(
            name="Hero",
            kind="character",
            training_status="training",
            ref_image_paths=["/tmp/ref1.jpg"],
        )
        db.session.add(s)
        db.session.flush()
        smp = SubjectSample(
            subject_id=s.id, index=0, status="done", approved=True,
            image_path="/tmp/gen1.jpg",
        )
        db.session.add(smp)
        db.session.commit()
        sid = s.id
        smp_id = smp.id

    lora_path = tmp_path / "hero.safetensors"
    # Verified-real checks require a non-tiny weights file + sidecar flag.
    lora_path.write_bytes(b"x" * 200)
    sidecar = lora_path.with_suffix(".json")
    sidecar.write_text(json.dumps({"mock": False, "subject_id": sid, "backend": "real"}))

    def fake_impl(subject_id, job_id=None):
        return {
            "status": "ok",
            "lora_path": str(lora_path),
            "lora_version": 1,
            "used_images": ["/tmp/ref1.jpg", "/tmp/gen1.jpg"],
        }

    monkeypatch.setattr(
        "backend.tasks.lora_trainer_tasks._train_impl", fake_impl,
    )
    # Skip smoke test (would need GPU/plugins)
    monkeypatch.setattr(
        "backend.services.lora_posttrain_smoke.run_lora_smoke_test",
        lambda **kw: {"ok": True},
    )

    with app.app_context():
        train_subject_lora_for_subject(sid)
        s = db.session.get(Subject, sid)
        smp = db.session.get(SubjectSample, smp_id)
        assert s.training_status == "trained"
        assert s.last_trained_image_paths == ["/tmp/ref1.jpg", "/tmp/gen1.jpg"]
        assert smp.promoted_to_training is True
        assert "/tmp/gen1.jpg" in (s.ref_image_paths or [])
