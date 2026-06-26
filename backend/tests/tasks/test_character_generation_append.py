"""Character Generator append-mode: a new batch STACKS onto curated (approved)
samples instead of wiping them, and never re-renders the kept keepers.

Mocks the externals (LLM plan, ComfyUI image gen, GPU gate, plugin bridge) so the
test exercises only the DB/index/append logic.
"""
import pytest
from pathlib import Path
from contextlib import contextmanager

try:
    from flask import Flask
    from backend.models import db, Subject, SubjectSample
    import backend.tasks.character_generation_tasks as cg
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


class _FakeGate:
    @contextmanager
    def gpu_exclusive(self, *a, **k):
        yield


class _FakeImageGen:
    def __init__(self, *a, **k):
        pass

    def generate_image(self, *, prompt, output_path, **k):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny stub
        return output_path


def _wire_mocks(monkeypatch, tmp_path, n_shots):
    """Patch the externals generate_samples imports at call time."""
    plan = {
        "bible": "test bible",
        "trigger_word": "sage_harlow",
        "shots": [{"index": i, "angle": f"a{i}", "image_prompt": f"sage_harlow shot {i}"}
                  for i in range(n_shots)],
    }
    monkeypatch.setattr(
        "backend.services.character_generator_service.generate_character_sheet",
        lambda **k: plan)
    monkeypatch.setattr(
        "backend.services.comfyui_image_generator.ComfyUIImageGenerator", _FakeImageGen)
    monkeypatch.setattr("backend.services.job_operation_gate.get_gate", lambda: _FakeGate())
    monkeypatch.setattr(
        "backend.services.plugin_bridge.ensure_plugins_for_stage", lambda *a, **k: None)
    # Redirect sample images into tmp so the test never touches repo data/.
    monkeypatch.setattr(cg, "_sample_output_dir",
                        lambda sid: Path(tmp_path) / str(sid))


def _make_subject(app):
    with app.app_context():
        s = Subject(name="Sage Harlow", kind="character", trigger_word="sage_harlow")
        db.session.add(s)
        db.session.commit()
        return s.id


def test_append_keeps_approved_and_offsets_new_indices(app, tmp_path, monkeypatch):
    sid = _make_subject(app)
    # Pre-seed two APPROVED keepers at non-contiguous indices (like real approvals).
    with app.app_context():
        for idx in (11, 18):
            db.session.add(SubjectSample(subject_id=sid, index=idx, status="done",
                                         approved=True, image_path=f"/x/sample_{idx}.png"))
        # plus a stale un-approved reject that append should clear out
        db.session.add(SubjectSample(subject_id=sid, index=5, status="done", approved=False))
        db.session.commit()

    _wire_mocks(monkeypatch, tmp_path, n_shots=3)
    with app.app_context():
        cg.generate_samples(sid, append=True)

    with app.app_context():
        rows = SubjectSample.query.filter_by(subject_id=sid).order_by(SubjectSample.index).all()
        approved = [r for r in rows if r.approved]
        new = [r for r in rows if not r.approved]
        # The 2 approved keepers survive untouched...
        assert sorted(r.index for r in approved) == [11, 18]
        # ...the stale reject (index 5) was cleared...
        assert all(r.index != 5 for r in new)
        # ...and the 3 new rows are offset ABOVE the max kept index (18 -> 19,20,21).
        assert sorted(r.index for r in new) == [19, 20, 21]
        assert all(r.status == "done" for r in new)  # only new rows generated


def test_replace_mode_wipes_everything(app, tmp_path, monkeypatch):
    sid = _make_subject(app)
    with app.app_context():
        db.session.add(SubjectSample(subject_id=sid, index=11, status="done", approved=True))
        db.session.commit()

    _wire_mocks(monkeypatch, tmp_path, n_shots=2)
    with app.app_context():
        cg.generate_samples(sid, append=False)

    with app.app_context():
        rows = SubjectSample.query.filter_by(subject_id=sid).order_by(SubjectSample.index).all()
        # Clean slate: old approved gone, fresh batch at 0,1.
        assert sorted(r.index for r in rows) == [0, 1]
        assert not any(r.approved for r in rows)
