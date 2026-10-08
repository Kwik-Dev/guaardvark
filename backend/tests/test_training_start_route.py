"""A training job created without starting can be started from the Training
page: the start applies the same checks as creating the job, queues the
fine-tune on the training worker once, and refuses anything that is not a
fine-tune waiting to start.

Seams: finetune_model_task.apply_async (read by the route at call time),
training_libraries.unavailable_reason and training_base_models.refusal_for_job.
Flask test client over an in-memory SQLite database; nothing is queued."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingDataset, TrainingJob, db
from backend.tasks import training_tasks as tt

MODEL = "Qwen/Qwen2.5-1.5B-Instruct"


@pytest.fixture
def checks(monkeypatch):
    """Libraries installed and the base model downloaded unless a test says otherwise."""
    state = SimpleNamespace(libraries=None, base_model=None)
    monkeypatch.setattr("backend.services.training_libraries.unavailable_reason", lambda: state.libraries)
    monkeypatch.setattr("backend.services.training_base_models.refusal_for_job",
                        lambda model_id, vision=False: state.base_model)
    return state


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def apply_async(args=None, kwargs=None, **options):
        if calls and calls[-1] == "refuse":
            from backend.celery_dispatch import TaskNotStarted
            raise TaskNotStarted("training.finetune_model", options.get("queue"), "broker unreachable")
        calls.append({"args": args, "kwargs": kwargs, **options})
        return SimpleNamespace(id=f"task-{len(calls)}")

    monkeypatch.setattr(tt.finetune_model_task, "apply_async", apply_async)
    return calls


@pytest.fixture
def client(checks, tmp_path):
    from backend.api.training.routes import training_bp

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _dataset(tmp_path):
    data = tmp_path / "notes.jsonl"
    data.write_text(json.dumps({"instruction": "Name a colour.", "output": "Blue."}) + "\n")
    ds = TrainingDataset(name="notes", path=str(data))
    db.session.add(ds)
    db.session.commit()
    return ds


def _pending(client, tmp_path):
    response = client.post("/api/training/jobs", json={
        "name": "later", "base_model": MODEL, "dataset_id": _dataset(tmp_path).id,
        "config": {"batch_size": 2}})
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert data["status"] == "pending" and data["celery_task_id"] is None
    return data["id"]


def _job(pk):
    db.session.expire_all()
    return db.session.get(TrainingJob, pk)


def test_a_pending_job_starts_once_on_the_training_worker(client, sent, tmp_path):
    pk = _pending(client, tmp_path)

    response = client.post(f"/api/training/jobs/{pk}/start")

    assert response.status_code == 200
    (call,) = sent
    assert call["queue"] == "training" and call["kwargs"] == {"resume": False}
    assert call["args"][0] == _job(pk).job_id and call["args"][1] == {"batch_size": 2}
    job = _job(pk)
    assert job.status == "running" and job.celery_task_id == "task-1"

    again = client.post(f"/api/training/jobs/{pk}/start")
    assert again.status_code == 400 and len(sent) == 1


def test_start_applies_the_checks_creating_the_job_did(client, sent, checks, tmp_path):
    pk = _pending(client, tmp_path)

    checks.base_model = "Qwen2.5 1.5B Instruct is not downloaded yet."
    refused = client.post(f"/api/training/jobs/{pk}/start")
    assert refused.status_code == 400
    assert refused.get_json()["error"]["code"] == "BASE_MODEL_UNAVAILABLE"

    checks.base_model = None
    checks.libraries = "Training needs unsloth, which is not installed."
    missing = client.post(f"/api/training/jobs/{pk}/start")
    assert missing.status_code == 409

    checks.libraries = None
    os.remove(db.session.get(TrainingDataset, _job(pk).dataset_id).path)
    gone = client.post(f"/api/training/jobs/{pk}/start")
    assert gone.status_code == 400 and "does not exist" in gone.get_json()["error"]["message"]

    assert sent == [] and _job(pk).status == "pending"


@pytest.mark.parametrize("status, stage, task_id", [
    ("running", "training", "task-0"), ("completed", "training", "task-0"),
    ("failed", "training", "task-0"), ("pending", "parsing", None), ("pending", "pending", "task-0"),
])
def test_only_a_fine_tune_that_never_started_can_start(client, sent, tmp_path, status, stage, task_id):
    ds = _dataset(tmp_path)
    job = TrainingJob(job_id="j", name="j", base_model=MODEL, dataset_id=ds.id, status=status,
                      pipeline_stage=stage, celery_task_id=task_id, config_json="{}")
    db.session.add(job)
    db.session.commit()

    response = client.post(f"/api/training/jobs/{job.id}/start")

    assert response.status_code == 400
    assert sent == []
    assert _job(job.id).status == status


def test_a_queue_that_does_not_take_the_task_leaves_the_job_pending(client, sent, tmp_path):
    pk = _pending(client, tmp_path)
    sent.append("refuse")

    response = client.post(f"/api/training/jobs/{pk}/start")

    assert response.status_code == 503
    job = _job(pk)
    assert job.status == "pending" and job.celery_task_id is None


def test_an_unknown_job_is_not_found(client, sent):
    assert client.post("/api/training/jobs/999/start").status_code == 404
