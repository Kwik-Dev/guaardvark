"""The training filter drops pairs scored below min_score, and says so when
the pairs carry no score instead of implying it filtered them."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingJob, db
from backend.tasks import training_tasks as tt

INSTRUCTION = "Explain what a held-out split is for."
OUTPUT = "It keeps some rows out of training so the model can be measured on them."


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    monkeypatch.setattr(tt, "PROCESSED_DIR", tmp_path)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def task_bodies_run_in_this_app(monkeypatch):
    """Calling a Celery task object goes through the worker's ContextTask,
    which pushes the worker's own Flask app and with it DATABASE_URL. The
    tests call each task's .run instead, and the in-process calls the
    pipeline makes resolve to .run too, so every query lands in this file's
    in-memory database."""
    for name in ("parse_transcripts_task", "filter_dataset_task", "finetune_model_task",
                 "export_gguf_task", "import_ollama_task", "full_training_pipeline_task"):
        monkeypatch.setattr(tt, name, getattr(tt, name).run)


@pytest.fixture
def progress(monkeypatch):
    """Every progress message the task emits, as (percent, message, status)."""
    sent = []

    def record(job_id, pct, message, status="processing", metrics=None):
        sent.append((pct, message, status))

    monkeypatch.setattr(tt, "_emit_progress", record)
    return sent


def _job(job_id="filter-job", status="running", config=None):
    job = TrainingJob(job_id=job_id, name="filter", status=status, pipeline_stage="filtering",
                      config_json=json.dumps(config or {}))
    db.session.add(job)
    db.session.commit()
    return job_id


def _row(job_id):
    db.session.expire_all()
    return db.session.query(TrainingJob).filter_by(job_id=job_id).one()


def _pair(**extra):
    return {"instruction": INSTRUCTION, "output": OUTPUT, **extra}


def _write_jsonl(path, rows):
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return str(path)


def _kept(result):
    with open(result["output_path"]) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_pairs_scored_below_min_score_are_dropped(app, progress, tmp_path):
    job_id = _job()
    src = _write_jsonl(tmp_path / "in.jsonl", [_pair(_quality_score=0.3), _pair(score=0.9)])

    result = tt.filter_dataset_task(job_id, src, 0.8)

    kept = _kept(result)
    assert len(kept) == 1 and kept[0]["score"] == 0.9
    saved = json.loads(_row(job_id).config_json)
    assert saved["min_score_applied"] is True
    assert saved["below_min_score"] == 1
    assert saved["filtered_count"] == 1
    assert "min_score_note" not in saved
    assert "1 below min_score 0.8" in progress[-1][1]


def test_unscored_pairs_are_kept_and_the_job_says_min_score_was_not_applied(app, progress, tmp_path):
    job_id = _job()
    src = _write_jsonl(tmp_path / "in.jsonl", [_pair(), _pair(), _pair()])

    result = tt.filter_dataset_task(job_id, src, 0.8)

    assert len(_kept(result)) == 3
    saved = json.loads(_row(job_id).config_json)
    assert saved["min_score_applied"] is False
    assert saved["min_score_note"] == "min_score not applied: pairs have no score"
    messages = [m for _, m, _ in progress]
    assert any("min_score not applied: pairs have no score" in m for m in messages)
    assert progress[-1][2] == "complete"
    assert "min_score not applied: pairs have no score" in progress[-1][1]


def test_unscored_pairs_survive_beside_scored_ones(app, progress, tmp_path):
    job_id = _job()
    src = _write_jsonl(tmp_path / "in.jsonl", [_pair(score=0.2), _pair(), _pair(score="0.95")])

    result = tt.filter_dataset_task(job_id, src, 0.5)

    assert len(_kept(result)) == 2
    assert result["min_score_applied"] is True
    assert result["scored_count"] == 2
    assert "1 pairs without a score kept" in progress[-1][1]


def test_filter_route_refuses_a_min_score_that_is_not_a_number(app):
    from backend.api.training.routes import training_bp

    app.register_blueprint(training_bp)
    client = app.test_client()

    response = client.post("/api/training/pipeline/filter",
                           json={"input_path": "/data/in.jsonl", "min_score": "high"})

    assert response.status_code == 400
    assert db.session.query(TrainingJob).count() == 0
