import tempfile
from pathlib import Path

import pytest
from flask import Flask

from backend.models import db, Subject, SubjectSample
from backend.services.lora_pretrain_gate import (
    build_training_captions,
    caption_coverage_stats,
    is_bare_caption,
    validate_cast_training,
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


def _write_image(tmp: Path, name: str) -> str:
    p = tmp / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return str(p)


def test_validate_cast_training_passes_with_trigger_and_framing(app):
    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = [_write_image(tmp, f"img{i}.png") for i in range(4)]
            # Ref captions live in sidecars (vision), not invented sample.image_prompt.
            for i, p in enumerate(paths):
                Path(p).with_suffix(".txt").write_text(
                    f"a photo of hero_token, person, full body shot {i}\n",
                    encoding="utf-8",
                )
            s = Subject(
                name="Hero",
                kind="character",
                trigger_word="hero_token",
                ref_image_paths=paths,
            )
            db.session.add(s)
            db.session.commit()

            gate = validate_cast_training(s, paths)
            assert gate["pass"] is True
            assert gate["images"] == 4
            assert len(gate["captions"]) == 4
            assert gate["full_body_count"] >= 2


def test_validate_cast_training_fails_on_too_few_images(app):
    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = [_write_image(tmp, "only.png")]
            s = Subject(name="Hero", kind="character", trigger_word="hero_token", ref_image_paths=paths)
            db.session.add(s)
            db.session.commit()

            gate = validate_cast_training(s, paths, min_images=4)
            assert gate["pass"] is False
            assert any("at least" in f.lower() for f in gate["failures"])


def test_build_training_captions_prefers_ref_sidecar_over_stale_sample_prompt(app):
    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            p = _write_image(tmp, "one.png")
            Path(p).with_suffix(".txt").write_text(
                "a photo of tok, man, shaved head, sunglasses\n",
                encoding="utf-8",
            )
            s = Subject(name="Hero", kind="character", trigger_word="tok", ref_image_paths=[p])
            db.session.add(s)
            db.session.commit()
            db.session.add(SubjectSample(
                subject_id=s.id,
                index=0,
                image_path=p,
                image_prompt="tok, long auburn hair, invented body, neon alley",
                approved=True,
                status="done",
            ))
            db.session.commit()

            caps = build_training_captions(s, [p])
            assert "auburn" not in caps[0].lower()
            assert "shaved head" in caps[0].lower()
            assert "a photo of tok" in caps[0].lower()


def test_build_training_captions_recomposes_generated_sample_not_invented_prompt(app):
    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Generated sample path — NOT in ref_image_paths
            p = _write_image(tmp, "sample_0.png")
            s = Subject(
                name="Hero",
                kind="character",
                trigger_word="tok",
                ref_image_paths=[],
                training_settings_json={
                    "class_token": "man",
                    "bible_identity_marks": "shaved head",
                },
            )
            db.session.add(s)
            db.session.commit()
            db.session.add(SubjectSample(
                subject_id=s.id,
                index=0,
                image_path=p,
                angle="face-forward",
                framing="close-up",
                expression="neutral",
                lighting="soft",
                scene="stadium",
                image_prompt="tok, long auburn hair, cartoon bear, stadium",
                approved=True,
                status="done",
            ))
            db.session.commit()

            caps = build_training_captions(s, [p])
            assert "auburn" not in caps[0].lower()
            assert "cartoon bear" not in caps[0].lower()
            assert "a photo of tok" in caps[0].lower()
            assert "man" in caps[0].lower()


def test_is_bare_caption_and_coverage_warns(app):
    assert is_bare_caption("a photo of tok", "tok") is True
    assert is_bare_caption("tok, full body, red jacket, alley", "tok") is False

    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            paths = [_write_image(tmp, f"img{i}.png") for i in range(4)]
            s = Subject(
                name="Hero",
                kind="character",
                trigger_word="tok",
                ref_image_paths=paths,
            )
            db.session.add(s)
            db.session.commit()

            gate = validate_cast_training(s, paths, min_images=4)
            assert gate["pass"] is True
            assert gate["bare_captions"] == 4
            assert any("bare" in w.lower() for w in gate["warnings"])

            stats = caption_coverage_stats(s, paths)
            assert stats["bare_captions"] == 4
            assert stats["rich_captions"] == 0