"""P0.2 — unified video-project spine (schema mixin) + cross-kind registry facade.

Locks: both kinds share the lifecycle spine columns byte-for-byte (no DDL drift —
this repo can't ALTER existing tables), the isinstance marker works, and the registry
gives one interface (list_all / get / resume_all_kinds) over every kind.
"""
import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo, Production, VideoProjectLifecycleMixin
    from backend.services import video_project_registry as reg
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


@pytest.fixture
def sent(monkeypatch):
    """Capture celery.send_task so resume dispatch doesn't hit a broker."""
    class _Rec:
        def __init__(self): self.calls = []
        def send_task(self, name, args=None, **kw): self.calls.append((name, tuple(args or ())))
    rec = _Rec()
    import backend.celery_app as ca
    monkeypatch.setattr(ca, "celery", rec, raising=False)
    return rec


# --- Schema spine (mixin) ----------------------------------------------------

_SPINE = ["id", "name", "status", "current_stage", "settings_json", "error_blob",
          "created_at", "updated_at"]


def test_both_models_carry_the_mixin_marker():
    assert issubclass(Production, VideoProjectLifecycleMixin)
    assert issubclass(MusicVideo, VideoProjectLifecycleMixin)
    assert Production.KIND == "film"
    assert MusicVideo.KIND == "music_video"


@pytest.mark.parametrize("col", _SPINE)
def test_spine_columns_are_ddl_identical(col):
    # No migration safety net here — the spine must stay byte-identical between tables.
    p = Production.__table__.columns[col]
    m = MusicVideo.__table__.columns[col]
    assert str(p.type) == str(m.type)
    assert p.nullable == m.nullable
    assert p.primary_key == m.primary_key
    assert bool(p.index) == bool(m.index)


def test_kind_property_on_instance(app):
    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    assert mv.kind == "music_video"


# --- Registry facade ---------------------------------------------------------

def test_kinds_and_lookups():
    assert set(reg.kinds()) == {"film", "music_video"}
    assert reg.model_for("film") is Production
    assert reg.model_for("music_video") is MusicVideo


def test_get_resolves_per_kind(app):
    p = ProductionService(db.session).create(name="P", script_text="x", project_id=None)
    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    assert reg.get(db.session, "film", p.id) is p
    assert reg.get(db.session, "music_video", mv.id) is mv
    assert reg.get(db.session, "film", 99999) is None
    assert reg.get(db.session, "no_such_kind", 1) is None


def test_list_all_spans_kinds(app):
    ProductionService(db.session).create(name="P1", script_text="x", project_id=None)
    MusicVideoService(db.session).create(
        name="M1", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    rows = reg.list_all(db.session)
    assert len(rows) == 2
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["film"]["name"] == "P1"
    assert by_kind["music_video"]["name"] == "M1"
    # normalized spine shape — no kind-specific payload leaks in
    assert set(rows[0]) == {"kind", "id", "name", "status", "current_stage", "created_at", "updated_at"}


def test_resume_all_kinds_counts_per_kind(app, sent):
    # One in-flight row of each kind at a resumable (non-user-gated) stage.
    p = ProductionService(db.session).create(name="P", script_text="x", project_id=None)
    p.current_stage = "screenwriting"; p.status = "screenwriting"
    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    mv.current_stage = "analyzing"; mv.status = "analyzing"
    db.session.commit()

    out = reg.resume_all_kinds(db.session)
    assert out == {"film": 1, "music_video": 1}
    # the right tasks were dispatched
    names = {c[0] for c in sent.calls}
    assert "production.run_screenwriter" in names
    assert "music_video.run_analyzer" in names


def test_resume_all_kinds_isolates_failure(app, sent, monkeypatch):
    # If one kind's resume blows up, the other still resumes and the failed one → 0.
    mv = MusicVideoService(db.session).create(
        name="M", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    mv.current_stage = "analyzing"; mv.status = "analyzing"
    db.session.commit()

    def boom(self):
        raise RuntimeError("film resume exploded")
    monkeypatch.setattr(ProductionService, "resume_all", boom)

    out = reg.resume_all_kinds(db.session)
    assert out["film"] == 0          # failure isolated, not raised
    assert out["music_video"] == 1   # other kind unaffected
