import tempfile
from pathlib import Path

import pytest
from flask import Flask

from backend.models import db, Subject, SubjectSample
from backend.services.lora_pretrain_gate import validate_cast_training, build_training_captions


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
            s = Subject(
                name="Hero",
                kind="character",
                trigger_word="hero_token",
                ref_image_paths=paths,
            )
            db.session.add(s)
            db.session.commit()

            for i, p in enumerate(paths):
                db.session.add(SubjectSample(
                    subject_id=s.id,
                    index=i,
                    image_path=p,
                    image_prompt=f"hero_token, full body shot {i}",
                    approved=True,
                    status="done",
                ))
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


def test_build_training_captions_uses_sample_prompt(app):
    with app.app_context():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            p = _write_image(tmp, "one.png")
            s = Subject(name="Hero", kind="character", trigger_word="tok", ref_image_paths=[p])
            db.session.add(s)
            db.session.commit()
            db.session.add(SubjectSample(
                subject_id=s.id,
                index=0,
                image_path=p,
                image_prompt="tok in neon alley, three-quarter view",
                approved=True,
                status="done",
            ))
            db.session.commit()

            caps = build_training_captions(s, [p])
            assert caps[0] == "tok in neon alley, three-quarter view"