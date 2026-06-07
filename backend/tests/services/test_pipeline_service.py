"""PipelineService base — parity + dedup lock.

Guards the P0.1 extraction: ProductionService and MusicVideoService must share the
ONE base implementation of the lifecycle plumbing, not carry copies. If someone
re-adds a per-subclass override, the identity assertions below fail loudly.
"""
import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo, Production
    from backend.services.pipeline_service import PipelineService, TERMINAL_STATUSES, _coerce_error
    from backend.services.production_service import ProductionService
    from backend.services.music_video_service import MusicVideoService
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


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


_SHARED_METHODS = [
    "advance_if_predecessor", "fail_stage", "find_non_terminal",
    "dispatch_agent", "resume_all", "gpu_stage",
]


def test_both_services_subclass_the_base():
    assert issubclass(ProductionService, PipelineService)
    assert issubclass(MusicVideoService, PipelineService)


@pytest.mark.parametrize("name", _SHARED_METHODS)
def test_state_machine_methods_are_inherited_not_copied(name):
    # The whole point of the extraction: each shared method resolves to the SAME
    # function object on both subclasses and on the base. A re-introduced override
    # (a regression to the old cloned code) breaks identity here.
    base_fn = getattr(PipelineService, name)
    assert getattr(ProductionService, name) is base_fn
    assert getattr(MusicVideoService, name) is base_fn


def test_class_attrs_wired():
    assert ProductionService.model_cls is Production
    assert ProductionService.task_namespace == "production"
    assert MusicVideoService.model_cls is MusicVideo
    assert MusicVideoService.task_namespace == "music_video"
    # The dicts are non-empty and start at draft.
    assert ProductionService.valid_transitions["draft"] == "screenwriting"
    assert MusicVideoService.valid_transitions["draft"] == "analyzing"


def test_same_base_code_drives_both_kinds(app):
    # One implementation, two row types — advance the first stage of each.
    prod = ProductionService(db.session).create(name="P", script_text="x", project_id=None)
    assert ProductionService(db.session).advance_if_predecessor(prod.id, expected_predecessor="draft")
    db.session.refresh(prod)
    assert prod.current_stage == "screenwriting" and prod.status == "screenwriting"

    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None,
    )
    assert MusicVideoService(db.session).advance_if_predecessor(mv.id, expected_predecessor="draft")
    db.session.refresh(mv)
    assert mv.current_stage == "analyzing" and mv.status == "analyzing"


def test_advance_rejects_wrong_predecessor_in_base(app):
    # Negative case: a mismatched predecessor advances nothing (the atomic guard).
    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None,
    )
    assert MusicVideoService(db.session).advance_if_predecessor(mv.id, expected_predecessor="assembling") is False
    db.session.refresh(mv)
    assert mv.current_stage == "draft"


def test_coerce_error_and_terminal_reexports():
    # Back-compat: symbols still importable from the service modules.
    from backend.services.production_service import TERMINAL_STATUSES as P_TERM
    from backend.services.music_video_service import TERMINAL_STATUSES as M_TERM
    assert P_TERM is TERMINAL_STATUSES and M_TERM is TERMINAL_STATUSES
    assert _coerce_error(ValueError("boom")) == "boom"
    assert _coerce_error({"a": 1}) == {"a": 1}
