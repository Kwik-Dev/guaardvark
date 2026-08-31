import pytest
from flask import Flask

from backend.models import db, Subject


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


def test_dispatch_lora_train_creates_job_and_sends_celery(app, monkeypatch):
    sent = {}

    class _FakeCelery:
        def send_task(self, name, args):
            sent["name"] = name
            sent["args"] = args
            return type("Task", (), {"id": "celery-uuid-1"})()

    class _FakeProgress:
        def create_process(self, *_a, **_k):
            return "job-unified-1"

        def error_process(self, *_a, **_k):
            pass

    monkeypatch.setattr("backend.celery_app.celery", _FakeCelery())
    monkeypatch.setattr(
        "backend.utils.unified_progress_system.get_unified_progress",
        lambda: _FakeProgress(),
    )

    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="training")
        db.session.add(s)
        db.session.commit()
        sid = s.id

        from backend.services.lora_train_dispatch import dispatch_lora_train

        result = dispatch_lora_train(sid)

        assert result == {"task_id": "celery-uuid-1", "job_id": "job-unified-1"}
        assert sent["name"] == "lora_trainer.train_lora"
        assert sent["args"] == [sid, "job-unified-1"]

        row = db.session.get(Subject, sid)
        assert row.current_training_job_id == "job-unified-1"


def test_cancel_lora_train_marks_failed(app, monkeypatch):
    shutdown_called = {"n": 0}

    class _Trainer:
        def shutdown(self):
            shutdown_called["n"] += 1

    class _FakeProgress:
        def cancel_process(self, *_a, **_k):
            pass

    monkeypatch.setattr("plugins.lora_trainer.real_trainer._TRAINER", _Trainer())
    monkeypatch.setattr(
        "backend.utils.unified_progress_system.get_unified_progress",
        lambda: _FakeProgress(),
    )

    with app.app_context():
        s = Subject(
            name="Hero",
            kind="character",
            training_status="training",
            current_training_job_id="job-abc",
        )
        db.session.add(s)
        db.session.commit()

        from backend.services.lora_train_dispatch import cancel_lora_train

        result = cancel_lora_train(s.id)
        assert result["cancelled"] is True
        assert shutdown_called["n"] == 1

        row = db.session.get(Subject, s.id)
        assert row.training_status == "failed"
        assert row.training_error == "Cancelled by user"
        assert row.current_training_job_id is None


def test_dispatch_lora_train_requires_training_status(app):
    with app.app_context():
        s = Subject(name="Hero", kind="character", training_status="untrained")
        db.session.add(s)
        db.session.commit()

        from backend.services.lora_train_dispatch import dispatch_lora_train

        with pytest.raises(ValueError, match="must be in 'training' status"):
            dispatch_lora_train(s.id)