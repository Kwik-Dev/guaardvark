"""GET /api/generate/status answers a job's state instead of a 500, and
GET /api/jobs/unified:<id> finds a live progress job.

The real blueprints and progress system, with its records in a temporary
OUTPUT_DIR; socket emits are switched off.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.utils import unified_progress_system as ups


@pytest.fixture
def progress(monkeypatch, tmp_path):
    monkeypatch.setattr(ups.UnifiedProgressSystem, "_emit_event", lambda *a, **k: None)
    system = ups.UnifiedProgressSystem()
    system.initialize(output_dir=str(tmp_path))
    monkeypatch.setattr(ups, "get_unified_progress", lambda: system)
    monkeypatch.setattr(ups, "_unified_progress", system)
    return system


@pytest.fixture
def client(progress, monkeypatch):
    from backend.api import unified_generation_api
    from backend.api.unified_generation_api import unified_gen_bp
    from backend.api.unified_jobs_resource_api import unified_jobs_resource_bp

    monkeypatch.setattr(unified_generation_api, "get_unified_progress", lambda: progress)
    app = Flask(__name__)
    app.register_blueprint(unified_gen_bp)
    app.register_blueprint(unified_jobs_resource_bp)
    return app.test_client()


def test_a_live_job_reports_its_progress(client, progress):
    pid = progress.create_process(ups.ProcessType.FILE_GENERATION, "rows",
                                  additional_data={"output_filename": "out.csv"})
    progress.update_process(pid, 40, "Row 4 of 10")
    resp = client.get(f"/api/generate/status?job_id={pid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["job_id"] == pid and body["status"] == "processing" and body["progress"] == 40
    assert body["message"] == "Row 4 of 10" and body["is_complete"] is False
    assert body["additional_data"]["output_filename"] == "out.csv"
    assert body["source"] == "live"


def test_a_job_this_process_no_longer_holds_is_read_from_its_record(client, progress):
    pid = progress.create_process(ups.ProcessType.FILE_GENERATION, "rows")
    progress.complete_process(pid, "Done")
    with progress._lock:
        progress._active_processes.pop(pid, None)
    body = client.get(f"/api/generate/status?job_id={pid}").get_json()
    assert body["status"] == "complete" and body["is_complete"] is True
    assert body["message"] == "Done" and body["source"] == "record"


@pytest.mark.parametrize("job_id", ["never_existed", "../../../../etc/passwd"])
def test_an_unknown_or_outside_id_is_404(client, job_id):
    resp = client.get("/api/generate/status", query_string={"job_id": job_id})
    assert resp.status_code == 404
    assert resp.get_json()["job_id"] == job_id


def test_a_missing_id_is_400(client):
    assert client.get("/api/generate/status").status_code == 400


def test_the_unified_job_detail_finds_a_live_process(client, progress):
    pid = progress.create_process(ups.ProcessType.FILE_GENERATION, "rows")
    progress.update_process(pid, 25, "Quarter")
    resp = client.get(f"/api/jobs/unified:{pid}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == f"unified:{pid}" and body["status"] == "running"
    assert body["progress"] == 25.0 and body["label"] == "Quarter"
