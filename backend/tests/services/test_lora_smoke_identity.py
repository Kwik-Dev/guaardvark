"""The post-train smoke still carries no identity number until a real compare exists.

score_smoke_vs_refs used to divide the smoke PNG's byte count by the mean
reference byte count and the Cast page showed that as the identity score. These
pin the shipped state from the caller's side: score_smoke_vs_refs returns no
score and never names the size method, and _run_smoke_once still writes and
records the smoke image while storing "not measured".

The render, the GPU session and the LoRA sidecar are stubbed where
_run_smoke_once imports them; nothing here loads weights or reaches a GPU.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from backend.models import Subject, db
from backend.services import lora_posttrain_smoke
from backend.services import video_consistency_metrics as vcm
from backend.services.stills_pipeline import StillResult


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
def refs_and_smoke(tmp_path):
    """Two references and a smoke image of about the same byte count.

    Same-sized files are the case the old score called a match, whatever face
    they held.
    """
    refs = []
    for i in range(2):
        p = tmp_path / f"ref_{i}.png"
        Image.new("RGB", (64, 64), (200, 40 * i, 10)).save(p)
        refs.append(str(p))
    smoke = tmp_path / "smoke.png"
    Image.new("RGB", (64, 64), (10, 120, 200)).save(smoke)
    return refs, str(smoke)


def test_score_smoke_vs_refs_returns_no_score(refs_and_smoke):
    refs, smoke = refs_and_smoke
    out = vcm.score_smoke_vs_refs(refs, smoke)

    identity = out["identity"]
    assert identity["score"] is None
    assert identity["status"] == "not_measured"
    assert identity["method"] != "size"
    assert identity["reason"]


def test_score_smoke_vs_refs_never_reaches_the_size_ratio(refs_and_smoke, monkeypatch):
    refs, smoke = refs_and_smoke
    seen = []
    real = vcm.score_identity_preservation

    def _spy(ref_paths, candidate_path, *, method="hist", analyzer=None):
        seen.append(method)
        return real(ref_paths, candidate_path, method=method, analyzer=analyzer)

    monkeypatch.setattr(vcm, "score_identity_preservation", _spy)
    vcm.score_smoke_vs_refs(refs, smoke)
    assert "size" not in seen


def test_smoke_run_still_writes_the_image_and_stores_not_measured(app, tmp_path, refs_and_smoke, monkeypatch):
    refs, _ = refs_and_smoke
    lora_path = tmp_path / "loras" / "anna.safetensors"
    lora_path.parent.mkdir()
    lora_path.write_bytes(b"stub")

    @contextmanager
    def _no_gpu(*_a, **_k):
        yield None

    def _render(prompt, *, output_path, **_k):
        Image.new("RGB", (64, 64), (90, 90, 90)).save(output_path)
        return StillResult(success=True, image_path=output_path, metadata={"family": "sdxl"})

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", _no_gpu)
    monkeypatch.setattr("backend.services.gpu_resource_policy.compositor_vram_reserve_mb", lambda: 0)
    monkeypatch.setattr("backend.services.character_still_pipeline.render_character_still", _render)
    monkeypatch.setattr("backend.services.media_model_registry.read_lora_sidecar", lambda _p: {})

    with app.app_context():
        subject = Subject(name="Anna", kind="character", ref_image_paths=refs)
        db.session.add(subject)
        db.session.commit()

        out_path = str(lora_path.parent / "smoke" / f"smoke_{subject.id}.png")
        Path(out_path).parent.mkdir()
        result = lora_posttrain_smoke._run_smoke_once(
            subject_id=subject.id,
            lora_path=str(lora_path),
            prompt="anna_tok person, portrait",
            out_path=out_path,
            resolution=512,
            base_model_id=None,
            ref_image_paths=refs,
            subject=subject,
        )

        assert result["ok"] is True
        assert result["path"] == out_path
        assert Path(out_path).is_file()
        assert result["identity"]["score"] is None

        stored = db.session.get(Subject, subject.id).training_settings_json["smoke_identity"]
        assert stored["path"] == out_path
        assert stored["score"] is None
        assert stored["status"] == "not_measured"
        assert stored["method"] != "size"
