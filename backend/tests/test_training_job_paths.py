"""Training jobs keep an honest status through the full pipeline, jobs made on
the Training page train on their dataset, and vision runs accept resume.

Seams: the training scripts (finetune_model, finetune_vision, transcript_parser)
are imported by name at call time, so sys.modules stand-ins reach them; the
GPU claim is gpu_resource_policy.gpu_session, read at call time; parsed
transcripts go to parsed_datasets_dir, read at call time."""
import ast
import importlib.util
import json
import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingDataset, TrainingJob, db
from backend.tasks import training_tasks as tt

SCRIPTS_DIR = Path(tt.__file__).resolve().parents[1] / "services" / "training" / "scripts"


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    monkeypatch.setattr(tt, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(tt, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(tt, "parsed_datasets_dir", lambda: tmp_path / "datasets")
    (tmp_path / "datasets").mkdir()
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def task_bodies_run_in_this_app(monkeypatch):
    """Calling a Celery task object goes through the worker's ContextTask and
    its own Flask app; the tests call each task's .run so every query lands
    in this file's in-memory database."""
    for name in ("parse_transcripts_task", "filter_dataset_task", "finetune_model_task",
                 "export_gguf_task", "import_ollama_task", "full_training_pipeline_task"):
        monkeypatch.setattr(tt, name, getattr(tt, name).run)


@pytest.fixture(autouse=True)
def base_model_on_disk(monkeypatch, tmp_path):
    """The job's base model counts as declared and downloaded; the trainer
    stand-ins receive its snapshot folder."""
    snapshot = tmp_path / "snapshot"
    monkeypatch.setattr("backend.services.training_base_models.resolve_for_training",
                        lambda model_id: ({"id": model_id}, str(snapshot)))
    monkeypatch.setattr("backend.services.training_base_models.refusal_for_job",
                        lambda model_id, vision=False: None)
    return str(snapshot)


@pytest.fixture(autouse=True)
def no_gpu_claim(monkeypatch):
    @contextmanager
    def no_gpu_session(*args, **kwargs):
        yield True

    monkeypatch.setattr("backend.services.gpu_resource_policy.gpu_session", no_gpu_session)


@pytest.fixture
def progress(monkeypatch):
    sent = []

    def record(job_id, pct, message, status="processing", metrics=None):
        sent.append((pct, message, status))

    monkeypatch.setattr(tt, "_emit_progress", record)
    return sent


def _script_params(name):
    """Parameter names of a training script's finetune(), read from its source
    so the check needs none of the training libraries."""
    tree = ast.parse((SCRIPTS_DIR / f"{name}.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "finetune")
    return {a.arg for a in fn.args.args}


@pytest.fixture
def trainer(monkeypatch, tmp_path):
    """Stands in for both training scripts. Each call must use only parameters
    the real script's finetune() declares, which is what a vision run broke."""
    state = SimpleNamespace(calls=[], fail_after_checkpoint=False)

    def make(script):
        params = _script_params(script)

        def finetune(**kwargs):
            unknown = set(kwargs) - params
            if unknown:
                raise TypeError(f"{script}.finetune() got unexpected keyword arguments {sorted(unknown)}")
            call = dict(kwargs, script=script)
            call["train_rows"] = Path(kwargs["data_path"]).read_text().splitlines()
            state.calls.append(call)
            model_dir = tmp_path / "models" / kwargs["output_name"]
            (model_dir / "lora").mkdir(parents=True, exist_ok=True)
            if state.fail_after_checkpoint:
                (model_dir / "checkpoints" / "checkpoint-100").mkdir(parents=True, exist_ok=True)
                (model_dir / "checkpoints" / "checkpoint-200").mkdir(parents=True, exist_ok=True)
                raise RuntimeError("CUDA out of memory")
            if kwargs.get("eval_data_path") and kwargs.get("eval_callback"):
                kwargs["eval_callback"]({"adapter_loss": 1.2, "base_loss": 1.5, "rows": 1})
            return str(model_dir)

        module = types.ModuleType(script)
        module.finetune = finetune
        monkeypatch.setitem(sys.modules, script, module)

    make("finetune_model")
    make("finetune_vision")
    return state


def _rows(n, start=0):
    return [json.dumps({"instruction": f"Question {i} about datasets?",
                        "output": f"Answer {i}, long enough to learn from."}) for i in range(start, start + n)]


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _row(job_id):
    db.session.expire_all()
    return db.session.query(TrainingJob).filter_by(job_id=job_id).one()


def _job(job_id="train-job", dataset_id=None, celery_task_id=None, **config):
    job = TrainingJob(job_id=job_id, name="train", base_model="org/base-model",
                      output_model_name="test-out", status="pending", dataset_id=dataset_id,
                      celery_task_id=celery_task_id, config_json=json.dumps({"steps": 5, **config}))
    db.session.add(job)
    db.session.commit()
    return job_id


def _dataset(path, name="notes"):
    dataset = TrainingDataset(name=name, path=str(path))
    db.session.add(dataset)
    db.session.commit()
    return dataset.id


# ---- the pipeline owns the job's status -------------------------------------


@pytest.fixture
def parser(monkeypatch):
    """Stands in for training/scripts/transcript_parser: every file yields 60 pairs."""
    class TranscriptParser:
        def __init__(self, output_dir=None):
            pass

        def parse_file(self, path):
            return [json.loads(line) for line in _rows(60)]

    module = types.ModuleType("transcript_parser")
    module.TranscriptParser = TranscriptParser
    monkeypatch.setitem(sys.modules, "transcript_parser", module)


def test_in_the_pipeline_the_parser_leaves_the_job_status_alone(app, progress, parser, tmp_path):
    source = _write(tmp_path / "chat.md", ["# a transcript"])
    job_id = _job(celery_task_id="pipeline-task")
    _row(job_id).status = "running"
    db.session.commit()

    result = tt.parse_transcripts_task(job_id, source, in_pipeline=True)

    row = _row(job_id)
    assert row.status == "running"
    assert row.completed_at is None
    assert row.celery_task_id == "pipeline-task"
    assert json.loads(row.config_json) == {"steps": 5}
    assert result["pairs_count"] == 60
    assert {status for _, _, status in progress} == {"processing"}
    low, high = tt.PIPELINE_PARSE_PROGRESS
    assert all(low <= pct <= high for pct, _, _ in progress)


def test_on_its_own_the_parser_still_completes_its_job(app, progress, parser, tmp_path):
    source = _write(tmp_path / "chat.md", ["# a transcript"])
    job_id = _job()

    tt.parse_transcripts_task(job_id, source)

    row = _row(job_id)
    assert row.status == "completed"
    report = json.loads(row.config_json)
    assert report["pairs_count"] == 60
    assert Path(report["output_path"]).parent == tmp_path / "datasets"
    assert progress[-1][2] == "complete"


def test_the_parser_is_loaded_from_the_training_plugin_where_it_ships(app, progress, tmp_path, monkeypatch):
    monkeypatch.delitem(sys.modules, "transcript_parser", raising=False)
    source = _write(tmp_path / "chat.md", [
        "User: How do plants make their food?",
        "Assistant: Plants make food by photosynthesis, using light in their leaves.",
    ])
    job_id = _job()

    tt.parse_transcripts_task(job_id, source)

    report = json.loads(_row(job_id).config_json)
    assert _row(job_id).status == "completed"
    assert report["pairs_count"] == 1
    rows = [json.loads(line) for line in Path(report["output_path"]).read_text().splitlines()]
    assert rows[0]["instruction"] == "How do plants make their food?"
    assert sys.modules["transcript_parser"].__file__.endswith(
        os.path.join("plugins", "training", "scripts", "transcript_parser.py"))


@pytest.fixture
def export_steps(monkeypatch):
    """The steps after training, recording the job row as export finds it,
    plus the Ollama unload the pipeline does first."""
    seen = {}

    def export_gguf(job_id, model_dir, quantization):
        row = _row(job_id)
        seen["at_export"] = (row.status, row.completed_at, row.celery_task_id, row.progress,
                             json.loads(row.config_json))
        return {"gguf_path": f"{model_dir}/model.gguf"}

    monkeypatch.setattr(tt, "export_gguf_task", export_gguf)
    monkeypatch.setattr(tt, "import_ollama_task", lambda job_id, model_dir, name: {"ollama_model_name": name})
    monkeypatch.setattr("backend.services.gpu_resource_coordinator.get_available_vram", lambda: {})
    monkeypatch.setattr("backend.services.gpu_resource_coordinator.unload_ollama_models",
                        lambda *a, **k: {"success": True, "models_unloaded": []})
    return seen


def test_the_pipeline_job_stays_running_from_parsing_through_training(
        app, progress, parser, trainer, export_steps, tmp_path):
    source = _write(tmp_path / "chat.md", ["# a transcript"])
    job_id = _job(celery_task_id="pipeline-task", input_path=source)

    tt.full_training_pipeline_task(job_id, {})

    status, completed_at, celery_task_id, pct, config = export_steps["at_export"]
    assert status == "running"
    assert completed_at is None
    assert celery_task_id == "pipeline-task"
    assert pct == tt.PIPELINE_TRAIN_PROGRESS[1]
    assert config["parse_report"]["pairs_count"] == 60
    assert config["eval"]["gate"]["export_allowed"] is True
    # Only the pipeline itself announces completion.
    completes = [message for _, message, status in progress if status == "complete"]
    assert len(completes) == 1 and completes[0].startswith("Full pipeline complete")
    assert _row(job_id).status == "completed"


# ---- vision runs ------------------------------------------------------------


def test_a_vision_run_starts_and_resumes_without_a_type_error(app, progress, trainer, tmp_path):
    data = _write(tmp_path / "vision.jsonl", _rows(20))
    job_id = _job(data_path=data, images_path=str(tmp_path / "images"))

    tt.finetune_model_task(job_id, {})
    tt.finetune_model_task(job_id, {}, resume=True)

    first, resumed = trainer.calls
    assert first["script"] == resumed["script"] == "finetune_vision"
    assert first["resume"] is False and resumed["resume"] is True
    assert _row(job_id).status == "completed"


@pytest.fixture
def vision_script(monkeypatch, tmp_path):
    """The real finetune_vision module with its training libraries replaced by
    recorders, and the real finetune_model for find_last_checkpoint."""
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF",
                       os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"))

    def load(name):
        spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    monkeypatch.setitem(sys.modules, "finetune_model", load("finetune_model"))
    script = load("finetune_vision")
    monkeypatch.setattr(script, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(script.torch.cuda, "empty_cache", lambda: None)

    trainers = []

    class SFTTrainer:
        def __init__(self, **kwargs):
            self.train_calls = []
            trainers.append(self)

        def train(self, **kwargs):
            self.train_calls.append(kwargs)

    class Saves:
        def save_pretrained(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)

    class Dataset:
        def __init__(self, rows):
            self.rows = rows

        @classmethod
        def from_list(cls, rows):
            return cls(rows)

        def cast_column(self, *args):
            return self

        def map(self, fn):
            return self

        def __len__(self):
            return len(self.rows)

    unsloth = types.ModuleType("unsloth")
    unsloth.FastVisionModel = SimpleNamespace(from_pretrained=lambda **k: (Saves(), Saves()),
                                              get_peft_model=lambda model, **k: model)
    unsloth.is_bf16_supported = lambda: False
    unsloth.UnslothVisionDataCollator = lambda model, tokenizer: None
    trl = types.ModuleType("trl")
    trl.SFTTrainer = SFTTrainer
    transformers = types.ModuleType("transformers")
    transformers.TrainingArguments = lambda **k: k
    datasets = types.ModuleType("datasets")
    datasets.Dataset = Dataset
    datasets.Image = lambda: None
    for name, module in (("unsloth", unsloth), ("trl", trl), ("transformers", transformers),
                         ("datasets", datasets)):
        monkeypatch.setitem(sys.modules, name, module)

    data = _write(tmp_path / "vision.jsonl", [json.dumps({
        "image": "a.png", "conversations": [{"role": "user", "content": "What is this?"},
                                            {"role": "assistant", "content": "A cat."}]})])
    return SimpleNamespace(module=script, trainers=trainers, data=data,
                           out=tmp_path / "models" / "vision-out")


def test_the_vision_trainer_resumes_from_its_newest_checkpoint(vision_script):
    for step in (100, 200):
        (vision_script.out / "checkpoints" / f"checkpoint-{step}").mkdir(parents=True)

    vision_script.module.finetune("org/base-vl", vision_script.data, "images",
                                  output_name="vision-out", resume=True)

    calls = vision_script.trainers[0].train_calls
    assert calls == [{"resume_from_checkpoint": str(vision_script.out / "checkpoints" / "checkpoint-200")}]


def test_the_vision_trainer_starts_fresh_without_resume_or_a_checkpoint(vision_script):
    vision_script.module.finetune("org/base-vl", vision_script.data, "images", output_name="vision-out")
    vision_script.module.finetune("org/base-vl", vision_script.data, "images",
                                  output_name="vision-out", resume=True)

    assert [t.train_calls for t in vision_script.trainers] == [[{}], [{}]]


def test_a_failed_run_is_resumable_from_the_checkpoints_it_wrote(app, progress, trainer, tmp_path):
    trainer.fail_after_checkpoint = True
    data = _write(tmp_path / "data.jsonl", _rows(20))
    job_id = _job(data_path=data)

    with pytest.raises(RuntimeError):
        tt.finetune_model_task(job_id, {})

    row = _row(job_id)
    assert row.status == "failed"
    assert row.is_resumable is True
    assert row.checkpoint_path.endswith("checkpoint-200")


# ---- jobs made on the Training page -----------------------------------------


@pytest.fixture
def client(app, monkeypatch):
    from backend.api.training.routes import training_bp

    # The training libraries count as installed; the trainer itself is a stand-in.
    monkeypatch.setattr("backend.services.training_libraries.unavailable_reason", lambda: None)
    app.register_blueprint(training_bp)
    return app.test_client()


def _create(client, dataset_id, **extra):
    return client.post("/api/training/jobs", json={
        "name": "page job", "base_model": "org/base-model", "output_model_name": "test-out",
        "dataset_id": dataset_id, "config": {"steps": 5, "images_path": None}, **extra})


def test_a_page_created_job_trains_on_its_dataset_file(app, client, progress, trainer, tmp_path):
    lines = _rows(120)
    data = _write(tmp_path / "sets" / "notes.jsonl", lines)
    response = _create(client, _dataset(data))
    assert response.status_code == 201
    job_id = response.get_json()["data"]["job_id"]
    assert "data_path" not in json.loads(_row(job_id).config_json)

    tt.finetune_model_task(job_id, {})

    saved = json.loads(_row(job_id).config_json)
    assert saved["data_path"] == data
    assert saved["dataset_files"] == [data]
    call = trainer.calls[0]
    assert sorted(call["train_rows"] + Path(call["eval_data_path"]).read_text().splitlines()) == sorted(lines)
    assert _row(job_id).status == "completed"


def test_a_dataset_folder_of_several_files_is_combined_in_order(app, client, progress, trainer, tmp_path):
    folder = tmp_path / "sets" / "folder"
    first = _write(folder / "a.jsonl", _rows(3))
    second = _write(folder / "b.jsonl", _rows(4, start=3))
    _write(folder / "readme.txt", ["not training data"])
    response = _create(client, _dataset(folder))
    job_id = response.get_json()["data"]["job_id"]

    tt.finetune_model_task(job_id, {})

    saved = json.loads(_row(job_id).config_json)
    assert saved["dataset_files"] == [first, second]
    combined = Path(saved["data_path"])
    assert combined == tmp_path / "models" / "test-out" / "dataset.jsonl"
    assert combined.read_text().splitlines() == _rows(7)
    assert trainer.calls[0]["train_rows"] == _rows(7)  # too few rows to hold any out


@pytest.mark.parametrize("make_path, says", [
    (lambda tmp: tmp / "empty", "holds no .jsonl or .json file"),
    (lambda tmp: tmp / "missing.jsonl", "does not exist"),
    (lambda tmp: "https://example.com/data.jsonl", "is a URL"),
])
def test_the_page_refuses_a_dataset_the_trainer_cannot_read(app, client, tmp_path, make_path, says):
    (tmp_path / "empty").mkdir()
    response = _create(client, _dataset(make_path(tmp_path)))

    assert response.status_code == 400
    assert says in response.get_json()["error"]["message"]
    assert db.session.query(TrainingJob).count() == 0


@pytest.mark.parametrize("sent_name, saved_name", [
    ("", "Parse: transcripts"), (None, "Parse: transcripts"), ("  Mine ", "Mine"),
])
def test_a_parse_job_gets_a_name_from_its_folder_when_none_is_typed(app, client, monkeypatch, sent_name, saved_name):
    sent = []
    monkeypatch.setattr(tt, "parse_transcripts_task", SimpleNamespace(
        apply_async=lambda args=None, **kw: sent.append(args) or SimpleNamespace(id="parse-1")))
    body = {"input_path": "/data/transcripts/"}
    if sent_name is not None:
        body["name"] = sent_name

    response = client.post("/api/training/pipeline/parse", json=body)

    assert response.status_code == 201
    assert response.get_json()["data"]["name"] == saved_name
    assert sent[0][1] == "/data/transcripts/"


def test_the_page_refuses_an_unknown_dataset(app, client):
    response = _create(client, 999)

    assert response.status_code == 400
    assert db.session.query(TrainingJob).count() == 0
