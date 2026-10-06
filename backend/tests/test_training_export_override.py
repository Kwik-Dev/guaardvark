"""A run the export gate held can be exported on a person's explicit choice,
the choice is recorded on the job, and the export routes hand the export and
import tasks the training output folder.

Seams: celery.chain and each task's apply_async, read by the routes at call
time; nothing is sent to a worker."""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.tasks import training_tasks as tt

GATE_NOTE = ("Not exported: held-out loss 3.0000 is 100.0% worse than the base model's 1.5000 "
             "(allowed margin 5%). The adapter is kept at /models/out/lora; choose Export anyway "
             "on the job to export it")


@pytest.fixture
def client(tmp_path):
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


@pytest.fixture
def dispatched(monkeypatch):
    """Records what the routes would send to the worker."""
    import celery

    sent = SimpleNamespace(chains=[], tasks=[])

    def chain(*signatures):
        record = {"signatures": signatures}
        sent.chains.append(record)

        def apply_async(**kwargs):
            record["options"] = kwargs
            return SimpleNamespace(id="workflow-1")

        return SimpleNamespace(apply_async=apply_async)

    def recorder(name):
        def apply_async(args=None, kwargs=None, **options):
            sent.tasks.append((name, args, options))
            return SimpleNamespace(id=f"{name}-1")
        return apply_async

    monkeypatch.setattr(celery, "chain", chain)
    monkeypatch.setattr(tt.export_gguf_task, "apply_async", recorder("export"))
    monkeypatch.setattr(tt.import_ollama_task, "apply_async", recorder("import"))
    return sent


def _held_job(tmp_path, status=tt.WORSE_THAN_BASE, lora=True):
    model_dir = tmp_path / "models" / "test-out"
    if lora:
        (model_dir / "lora").mkdir(parents=True)
    config = {"steps": 5, "eval": {"measured": True, "adapter_loss": 3.0, "base_loss": 1.5,
                                   "gate": {"margin": 0.05, "export_allowed": False, "note": GATE_NOTE}}}
    job = TrainingJob(job_id="held-job", name="held", base_model="org/base-model",
                      output_model_name="test-out", status=status, pipeline_stage="training",
                      lora_path=str(model_dir / "lora"),
                      error_message=GATE_NOTE if status == tt.WORSE_THAN_BASE else None,
                      config_json=json.dumps(config))
    db.session.add(job)
    db.session.commit()
    return job.id, model_dir


def _row(pk):
    db.session.expire_all()
    return db.session.get(TrainingJob, pk)


def test_a_held_run_cannot_be_exported_before_the_override(client, dispatched, tmp_path):
    pk, _ = _held_job(tmp_path)

    response = client.post(f"/api/training/jobs/{pk}/export-to-ollama", json={"model_name": "mine"})

    assert response.status_code == 400
    assert dispatched.chains == [] and dispatched.tasks == []
    assert _row(pk).status == tt.WORSE_THAN_BASE


def test_the_override_is_recorded_with_who_when_and_the_gate_verdict(client, dispatched, tmp_path):
    pk, _ = _held_job(tmp_path)

    response = client.post(f"/api/training/jobs/{pk}/export-anyway",
                           json={"by": "person", "via": "training_page"})

    assert response.status_code == 200
    row = _row(pk)
    assert row.status == "completed"
    assert row.error_message is None
    config = json.loads(row.config_json)
    override = config["export_override"]
    assert override["by"] == "person"
    assert override["via"] == "training_page"
    assert override["at"].endswith("Z")
    assert override["held_status"] == tt.WORSE_THAN_BASE
    assert override["gate_note"] == GATE_NOTE
    assert "from_address" in override
    # The gate's own verdict stays as measured; nothing was exported yet.
    assert config["eval"]["gate"]["export_allowed"] is False
    assert "export_if_worse" not in config
    assert dispatched.chains == [] and dispatched.tasks == []


def test_a_held_run_exports_after_the_override(client, dispatched, tmp_path):
    pk, model_dir = _held_job(tmp_path)
    client.post(f"/api/training/jobs/{pk}/export-anyway", json={})

    response = client.post(f"/api/training/jobs/{pk}/export-to-ollama",
                           json={"model_name": "mine", "quantization": "q8_0"})

    assert response.status_code == 202
    (workflow,) = dispatched.chains
    export, register = workflow["signatures"]
    assert export.task == tt.export_gguf_task.name
    assert tuple(export.args) == ("held-job", str(model_dir), "q8_0")
    assert register.task == tt.import_ollama_task.name
    assert tuple(register.args) == ("held-job", str(model_dir), "mine")
    assert register.immutable
    assert workflow["options"] == {"queue": "training"}
    row = _row(pk)
    assert row.status == "running" and row.pipeline_stage == "exporting"
    assert json.loads(row.config_json)["export_override"]["by"] == "person"


@pytest.mark.parametrize("status", ["completed", "failed", "running"])
def test_only_a_held_run_can_be_exported_anyway(client, dispatched, tmp_path, status):
    pk, _ = _held_job(tmp_path, status=status)

    response = client.post(f"/api/training/jobs/{pk}/export-anyway", json={})

    assert response.status_code == 400
    assert _row(pk).status == status
    assert "export_override" not in json.loads(_row(pk).config_json)


def test_a_held_run_without_its_adapter_is_not_released(client, dispatched, tmp_path):
    pk, _ = _held_job(tmp_path, lora=False)

    response = client.post(f"/api/training/jobs/{pk}/export-anyway", json={})

    assert response.status_code == 400
    assert _row(pk).status == tt.WORSE_THAN_BASE


def test_an_unknown_job_is_not_found(client, dispatched):
    assert client.post("/api/training/jobs/999/export-anyway", json={}).status_code == 404


def test_the_gguf_export_route_hands_over_the_training_output_folder(client, dispatched, tmp_path):
    pk, model_dir = _held_job(tmp_path, status="completed")

    response = client.post(f"/api/training/jobs/{pk}/export", json={"quantization": "q4_k_m"})

    assert response.status_code == 202
    assert dispatched.tasks == [("export", ["held-job", str(model_dir), "q4_k_m"], {"queue": "training"})]


def test_the_import_routes_hand_over_the_folder_holding_the_gguf(client, dispatched, tmp_path):
    pk, model_dir = _held_job(tmp_path, status="completed")
    gguf = model_dir / "model-q4_k_m.gguf"
    gguf.write_bytes(b"gguf")
    row = _row(pk)
    row.gguf_path = str(gguf)
    db.session.commit()

    assert client.post(f"/api/training/jobs/{pk}/import-ollama", json={"model_name": "mine"}).status_code == 202
    row = _row(pk)
    row.status, row.pipeline_stage = "completed", "training"
    db.session.commit()
    assert client.post(f"/api/training/jobs/{pk}/export-to-ollama", json={"model_name": "mine"}).status_code == 202

    assert [args for _, args, _ in dispatched.tasks] == [["held-job", str(model_dir), "mine"]] * 2
