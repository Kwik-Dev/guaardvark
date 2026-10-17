"""A run the export gate held can be exported on a person's explicit choice,
the choice is recorded on the job, the export routes hand the export and
import tasks the training output folder, and no export starts for a base
model whose export to Ollama has not been verified, or while the GGUF
converter it runs is not installed.

Export itself folds the adapter into its base model in an offline subprocess,
converts the merged folder to one GGUF file with the installed converter, and
registers that file with `ollama create`, using the chat template the base
model's entry declares. Once Ollama has registered the GGUF the merged folder
is deleted; a failed step keeps it, and the next export merges again. A run
folder merged before Export went through GGUF still registers its merged
folder with `ollama create --quantize`, and keeps it.

Seams: celery.chain and each task's apply_async, read by the routes at call
time; training_base_models.export_verified and resolve_for_training,
training_gguf_converter.refusal_for and convert, training_runner.run and
subprocess.run (the ollama CLI), read at call time; nothing is sent to a
worker and nothing runs."""
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


@pytest.fixture(autouse=True)
def progress(monkeypatch):
    """The tasks' progress events are recorded here; the real emitter reaches
    the unified progress system, which builds the whole backend app."""
    sent = []
    monkeypatch.setattr(tt, "_emit_progress",
                        lambda job_id, pct, message, status="processing", metrics=None: sent.append((pct, message, status)))
    return sent


@pytest.fixture(autouse=True)
def verified(monkeypatch):
    """Export counts as verified for the job's base model unless a test says not."""
    state = {"verified": True}
    monkeypatch.setattr("backend.services.training_base_models.export_verified",
                        lambda model_id: state["verified"])
    return state


@pytest.fixture(autouse=True)
def converter(monkeypatch):
    """The GGUF converter counts as installed and checked with the job's
    family unless a test says not; convert writes the file it is asked for."""
    from backend.services import training_gguf_converter

    state = SimpleNamespace(refusal=None, calls=[], fail=None)
    monkeypatch.setattr(training_gguf_converter, "refusal_for", lambda entry: state.refusal)

    def convert(merged, out_file, level, *, log_path, **kwargs):
        state.calls.append({"merged": Path(merged), "out": Path(out_file), "level": level,
                            "log_path": Path(log_path)})
        if state.fail:
            raise training_gguf_converter.ConverterFailed(state.fail)
        Path(out_file).write_bytes(b"GGUF" * 10)
        return Path(out_file)

    monkeypatch.setattr(training_gguf_converter, "convert", convert)
    return state


MISSING = ("GGUF_CONVERTER_MISSING",
           "Export to Ollama converts the trained model to GGUF with llama.cpp's converter, which "
           "is not installed. Install it in Settings > Training libraries > GGUF converter.")


@pytest.fixture(autouse=True)
def ollama_version(monkeypatch):
    """The Ollama server's version as the export reads it; unmeasured unless a test says."""
    state = {"version": None}
    monkeypatch.setattr(tt, "ollama_server_version", lambda: state["version"])
    return state


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

    response = client.post(f"/api/training/jobs/{pk}/export", json={"quantization": "F16"})

    assert response.status_code == 202
    assert dispatched.tasks == [("export", ["held-job", str(model_dir), "f16"], {"queue": "training"})]
    assert _row(pk).quantization_level == "f16"


def test_no_level_asked_for_exports_at_the_default(client, dispatched, tmp_path):
    pk, model_dir = _held_job(tmp_path, status="completed")

    assert client.post(f"/api/training/jobs/{pk}/export-to-ollama", json={"model_name": "m"}).status_code == 202

    (workflow,) = dispatched.chains
    assert tuple(workflow["signatures"][0].args) == ("held-job", str(model_dir), "q8_0")


@pytest.mark.parametrize("path", ["export", "export-to-ollama"])
def test_a_level_the_converter_does_not_write_is_refused_with_the_choices(client, dispatched, tmp_path, path):
    pk, _ = _held_job(tmp_path, status="completed")

    response = client.post(f"/api/training/jobs/{pk}/{path}", json={"model_name": "m", "quantization": "q4_k_m"})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "UNSUPPORTED_LEVEL"
    assert "Q8_0, F16" in response.get_json()["error"]["message"]
    assert dispatched.chains == [] and dispatched.tasks == []
    assert _row(pk).status == "completed"


def test_without_the_converter_export_points_at_its_install(client, dispatched, tmp_path, converter):
    converter.refusal = MISSING
    pk, _ = _held_job(tmp_path, status="completed")

    for path in (f"/api/training/jobs/{pk}/export", f"/api/training/jobs/{pk}/export-to-ollama"):
        response = client.post(path, json={"model_name": "mine"})
        assert response.status_code == 409, path
        assert response.get_json()["error"]["code"] == "GGUF_CONVERTER_MISSING"
        assert "Settings > Training libraries > GGUF converter" in response.get_json()["error"]["message"]
    assert dispatched.chains == [] and dispatched.tasks == []
    assert _row(pk).status == "completed"


def test_the_job_list_says_what_export_waits_for_and_offers(client, tmp_path, converter):
    _held_job(tmp_path, status="completed")
    converter.refusal = MISSING

    (job,) = client.get("/api/training/jobs").get_json()["data"]

    assert job["export_verified"] is True
    assert job["export_blocked_code"] == "GGUF_CONVERTER_MISSING"
    assert "GGUF converter" in job["export_blocked"]
    assert [level["value"] for level in job["export_levels"]] == ["q8_0", "f16"]
    assert job["export_levels"][0]["default"] is True

    converter.refusal = None
    (job,) = client.get("/api/training/jobs").get_json()["data"]
    assert job["export_blocked"] is None and job["export_blocked_code"] is None


def test_a_gguf_at_another_level_is_exported_again(client, dispatched, tmp_path):
    pk, model_dir = _held_job(tmp_path, status="completed")
    gguf = model_dir / "test-out-q8_0.gguf"
    gguf.write_bytes(b"gguf")
    row = _row(pk)
    row.gguf_path, row.quantization_level = str(gguf), "q8_0"
    db.session.commit()

    response = client.post(f"/api/training/jobs/{pk}/export-to-ollama",
                           json={"model_name": "mine", "quantization": "f16"})

    assert response.status_code == 202
    (workflow,) = dispatched.chains
    assert tuple(workflow["signatures"][0].args) == ("held-job", str(model_dir), "f16")


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


def test_no_export_starts_for_a_base_model_not_verified(client, dispatched, tmp_path, verified):
    verified["verified"] = False
    pk, _ = _held_job(tmp_path, status="completed")

    for path in (f"/api/training/jobs/{pk}/export", f"/api/training/jobs/{pk}/export-to-ollama"):
        response = client.post(path, json={"model_name": "mine"})
        assert response.status_code == 409, path
        assert response.get_json()["error"]["code"] == "EXPORT_NOT_VERIFIED"
        assert "not verified for org/base-model" in response.get_json()["error"]["message"]
    assert dispatched.chains == [] and dispatched.tasks == []
    assert _row(pk).status == "completed"


def test_the_export_task_refuses_before_touching_the_job(client, tmp_path, verified):
    verified["verified"] = False
    pk, model_dir = _held_job(tmp_path, status="completed")

    with pytest.raises(tt.ExportNotVerified):
        tt.export_gguf_task.run("held-job", str(model_dir), "q8_0")

    row = _row(pk)
    assert row.status == "completed" and row.pipeline_stage == "training"


# ---- export: merge, convert to GGUF, then ollama create ---------------------------

QWEN = "Qwen/Qwen2.5-1.5B-Instruct"


def _trained_job(tmp_path, quantization=None):
    model_dir = tmp_path / "models" / "test-out"
    (model_dir / "lora").mkdir(parents=True)
    job = TrainingJob(job_id="trained", name="trained", base_model=QWEN, output_model_name="test-out",
                      status="completed", pipeline_stage="training", lora_path=str(model_dir / "lora"),
                      quantization_level=quantization, config_json="{}")
    db.session.add(job)
    db.session.commit()
    return job.id, model_dir


@pytest.fixture
def merge(monkeypatch, tmp_path):
    from backend.services import training_base_models

    calls = []
    snapshot = str(tmp_path / "snapshot")
    monkeypatch.setattr("backend.services.training_base_models.resolve_for_training",
                        lambda model_id: (training_base_models.get(model_id), snapshot))

    def run(command, spec, **kwargs):
        calls.append((command, spec, kwargs))
        merged = Path(spec["out_dir"])
        merged.mkdir(parents=True, exist_ok=True)
        (merged / "config.json").write_text("{}")
        return {"event": "done", "model_dir": str(merged)}

    monkeypatch.setattr("backend.services.training_runner.run", run)
    return SimpleNamespace(calls=calls, snapshot=snapshot)


@pytest.fixture
def ollama_cli(monkeypatch):
    import subprocess

    calls = []

    def run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="success", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", run)
    return calls


def test_export_merges_the_adapter_and_converts_it_to_gguf(client, tmp_path, merge, converter):
    pk, model_dir = _trained_job(tmp_path)

    result = tt.export_gguf_task.run("trained", str(model_dir), None)

    (command, spec, kwargs), = merge.calls
    assert command == "merge"
    assert spec["base_model"] == merge.snapshot
    assert spec["lora_dir"] == str(model_dir / "lora") and spec["out_dir"] == str(model_dir / "merged")
    assert kwargs["workdir"] == model_dir
    gguf = model_dir / "test-out-q8_0.gguf"
    (call,) = converter.calls
    assert call == {"merged": model_dir / "merged", "out": gguf, "level": "q8_0",
                    "log_path": model_dir / "train.log"}
    assert result == {"merged_path": str(model_dir / "merged"), "gguf_path": str(gguf),
                      "quantization": "q8_0"}
    row = _row(pk)
    assert row.status == "running" and row.pipeline_stage == "exporting"
    assert row.gguf_path == str(gguf) and row.quantization_level == "q8_0"


def test_import_registers_the_gguf_with_the_declared_template(client, tmp_path, merge, ollama_cli):
    from backend.services import training_base_models

    pk, model_dir = _trained_job(tmp_path)
    tt.export_gguf_task.run("trained", str(model_dir), "f16")

    tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    (call,) = ollama_cli
    assert call["cmd"] == ["ollama", "create", "my-model", "-f", str(model_dir / "Modelfile")]
    modelfile = (model_dir / "Modelfile").read_text()
    assert modelfile.startswith(f"FROM {model_dir / 'test-out-f16.gguf'}\n")
    assert f'TEMPLATE """{training_base_models.get(QWEN)["ollama_template"]}"""' in modelfile
    assert 'PARAMETER stop "<|im_end|>"' in modelfile
    row = _row(pk)
    assert row.status == "completed" and row.ollama_model_name == "my-model"


# ---- the merged copy goes once Ollama has the model --------------------------------

def _files(model_dir):
    return {str(p.relative_to(model_dir)) for p in model_dir.rglob("*")}


def test_a_registered_gguf_drops_the_merged_copy_and_nothing_else(client, tmp_path, merge, ollama_cli, progress):
    pk, model_dir = _trained_job(tmp_path)
    (model_dir / "lora" / "adapter_model.safetensors").write_bytes(b"A" * 10)
    (model_dir / "checkpoints" / "checkpoint-10").mkdir(parents=True)
    (model_dir / "heldout").mkdir()
    (model_dir / "heldout" / "heldout.jsonl").write_text("{}\n")
    tt.export_gguf_task.run("trained", str(model_dir), "q8_0")
    (model_dir / "merged" / "model.safetensors").write_bytes(b"W" * 1000)
    kept = {f for f in _files(model_dir) if not f.startswith("merged")}

    tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    assert not (model_dir / "merged").exists()
    assert _files(model_dir) == kept | {"Modelfile"}
    assert "test-out-q8_0.gguf" in kept and "lora/adapter_model.safetensors" in kept
    assert _row(pk).status == "completed"
    assert progress[-1][1].startswith("Ollama import complete: my-model (removed the merged copy")


def test_a_failed_import_keeps_the_merged_copy(client, tmp_path, merge, monkeypatch):
    import subprocess

    pk, model_dir = _trained_job(tmp_path)
    tt.export_gguf_task.run("trained", str(model_dir), "q8_0")
    monkeypatch.setattr(tt.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="Error: could not reach the Ollama server"))

    with pytest.raises(RuntimeError, match="ollama create failed"):
        tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    assert (model_dir / "merged" / "config.json").is_file()
    assert (model_dir / "test-out-q8_0.gguf").is_file()
    assert _row(pk).status == "failed"


def test_exporting_again_after_the_merged_copy_went_merges_again(client, tmp_path, merge, ollama_cli):
    pk, model_dir = _trained_job(tmp_path)
    tt.export_gguf_task.run("trained", str(model_dir), "q8_0")
    tt.import_ollama_task.run("trained", str(model_dir), "my-model")
    assert not (model_dir / "merged").exists()

    tt.export_gguf_task.run("trained", str(model_dir), "f16")

    assert len(merge.calls) == 2 and (model_dir / "merged" / "config.json").is_file()
    assert sorted(p.name for p in model_dir.glob("*.gguf")) == ["test-out-f16.gguf"]

    tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    assert not (model_dir / "merged").exists()
    assert (model_dir / "Modelfile").read_text().startswith(f"FROM {model_dir / 'test-out-f16.gguf'}\n")
    assert _row(pk).status == "completed" and _row(pk).quantization_level == "f16"


def test_only_the_merged_folder_a_gguf_export_here_came_from_is_dropped(tmp_path):
    model_dir = tmp_path / "run"
    (model_dir / "merged").mkdir(parents=True)
    (model_dir / "merged" / "config.json").write_text("{}")

    assert tt._drop_merged(model_dir, tmp_path / "elsewhere" / "run-q8_0.gguf") == 0
    assert tt._drop_merged(model_dir, model_dir / "run-q4_k_m.gguf") == 0
    assert (model_dir / "merged" / "config.json").is_file()

    assert tt._drop_merged(model_dir, model_dir / "run-q8_0.gguf") == 2
    assert not (model_dir / "merged").exists()


def test_a_merged_link_is_left_alone(tmp_path):
    shared = tmp_path / "shared-merge"
    shared.mkdir()
    (shared / "config.json").write_text("{}")
    model_dir = tmp_path / "run"
    model_dir.mkdir()
    (model_dir / "merged").symlink_to(shared)

    assert tt._drop_merged(model_dir, model_dir / "run-q8_0.gguf") == 0
    assert (model_dir / "merged").is_symlink() and (shared / "config.json").is_file()


def test_a_level_the_converter_does_not_write_fails_before_the_merge(client, tmp_path, merge, converter):
    pk, model_dir = _trained_job(tmp_path)

    with pytest.raises(ValueError, match="offers Q8_0, F16"):
        tt.export_gguf_task.run("trained", str(model_dir), "q4_k_m")

    assert merge.calls == [] and converter.calls == []
    assert _row(pk).status == "completed" and _row(pk).pipeline_stage == "training"


def test_a_failed_conversion_fails_the_job_with_the_reason(client, tmp_path, merge, converter):
    converter.fail = "The GGUF converter exited with code 1: ERROR: Model X is not supported"
    pk, model_dir = _trained_job(tmp_path)

    with pytest.raises(Exception, match="exited with code 1"):
        tt.export_gguf_task.run("trained", str(model_dir), "q8_0")

    row = _row(pk)
    assert row.status == "failed" and "Model X is not supported" in row.error_message
    assert row.gguf_path is None
    assert (model_dir / "merged" / "config.json").is_file()


def test_exporting_again_at_another_level_leaves_one_gguf(client, tmp_path, merge):
    pk, model_dir = _trained_job(tmp_path)
    tt.export_gguf_task.run("trained", str(model_dir), "q8_0")

    tt.export_gguf_task.run("trained", str(model_dir), "f16")

    assert sorted(p.name for p in model_dir.glob("*.gguf")) == ["test-out-f16.gguf"]
    assert _row(pk).gguf_path == str(model_dir / "test-out-f16.gguf")


def test_without_the_converter_the_export_task_refuses_before_merging(client, tmp_path, merge, converter):
    converter.refusal = MISSING
    pk, model_dir = _trained_job(tmp_path)

    with pytest.raises(tt.ExportNotVerified, match="GGUF converter"):
        tt.export_gguf_task.run("trained", str(model_dir), "q8_0")

    assert merge.calls == []
    assert _row(pk).status == "completed" and _row(pk).pipeline_stage == "training"


# ---- a run folder merged before Export went through GGUF -------------------------

def _merged_before(model_dir):
    (model_dir / "merged").mkdir()
    (model_dir / "merged" / "config.json").write_text("{}")


def test_a_folder_merged_before_registers_the_merged_model(client, tmp_path, ollama_cli):
    pk, model_dir = _trained_job(tmp_path, quantization="q4_k_m")
    _merged_before(model_dir)

    tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    (call,) = ollama_cli
    assert call["cmd"] == ["ollama", "create", "my-model", "-f", str(model_dir / "Modelfile"),
                           "--quantize", "q4_K_M"]
    assert (model_dir / "Modelfile").read_text().startswith(f"FROM {model_dir / 'merged'}\n")
    assert _row(pk).status == "completed"
    assert (model_dir / "merged" / "config.json").is_file()


def test_a_level_ollama_cannot_quantize_to_is_refused_with_the_choices(client, tmp_path, ollama_cli):
    pk, model_dir = _trained_job(tmp_path, quantization="q5_k_m")
    _merged_before(model_dir)

    with pytest.raises(ValueError, match="cannot quantize a merged model to q5_k_m"):
        tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    assert ollama_cli == []
    assert _row(pk).status == "failed"


# ---- an Ollama measured unable to import the merged model ----------------------------

def test_export_through_gguf_starts_on_an_ollama_that_cannot_import_safetensors(
        client, dispatched, tmp_path, ollama_version):
    ollama_version["version"] = "0.40.1"
    pk, _ = _held_job(tmp_path, status="completed")

    response = client.post(f"/api/training/jobs/{pk}/export-to-ollama", json={"model_name": "mine"})

    assert response.status_code == 202
    assert len(dispatched.chains) == 1


def test_a_held_run_is_not_released_while_the_converter_is_missing(client, dispatched, tmp_path, converter):
    converter.refusal = MISSING
    pk, _ = _held_job(tmp_path)

    response = client.post(f"/api/training/jobs/{pk}/export-anyway", json={})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "GGUF_CONVERTER_MISSING"
    assert _row(pk).status == tt.WORSE_THAN_BASE
    assert "export_override" not in json.loads(_row(pk).config_json)


def test_a_folder_merged_before_is_not_sent_to_that_ollama(client, tmp_path, ollama_cli, ollama_version):
    _, model_dir = _trained_job(tmp_path)
    _merged_before(model_dir)
    ollama_version["version"] = "0.40.1"

    with pytest.raises(RuntimeError, match="installed Ollama 0.40.1 cannot import the merged model"):
        tt.import_ollama_task.run("trained", str(model_dir), "my-model")

    assert ollama_cli == []


@pytest.mark.parametrize("tail", [
    'Error: unsupported --quantize "q4_K_M": supported types are int4, int8, nvfp4, mxfp4, mxfp8',
    'Error: unsupported MLX architecture: model "Qwen2ForCausalLM"',
])
def test_both_import_refusals_read_as_the_plain_reason(ollama_version, tail):
    ollama_version["version"] = "0.41.0"  # an unmeasured version answering the same way

    message = tt.ollama_create_error(tail)

    assert message.startswith("The installed Ollama 0.41.0 cannot import the merged model as safetensors")
    assert "GGUF converter" in message
    assert tail in message


def test_any_other_ollama_failure_keeps_its_text(ollama_version):
    assert tt.ollama_create_error("Error: no space left on device") == (
        "ollama create failed: Error: no space left on device")
