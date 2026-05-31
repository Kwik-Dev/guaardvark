"""Regression tests for the Batch-2 security fixes (2026-05-30).

Each proves the guard FIRES on the malicious case (and lets the legit case through).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from flask import Flask
from werkzeug.exceptions import NotFound


# ---- batch_video <batch_id> path-traversal guard ---------------------------
def test_batch_id_traversal_rejected():
    from backend.api.batch_video_generation_api import _reject_unsafe_batch_id
    for bad in ["../x", "..", "a/b", "foo.bar", "x;rm", ""]:
        with pytest.raises(NotFound):
            _reject_unsafe_batch_id("ep", {"batch_id": bad})
    # legit flat tokens pass the guard (no exception)
    for ok in ["VideoBatch_05-30-2026_003", "batch-1", "abc123"]:
        _reject_unsafe_batch_id("ep", {"batch_id": ok})


# ---- auth_guard mutation protection ----------------------------------------
def test_auth_guard_protects_job_task_scheduler_mutations():
    from backend.utils.auth_guard import _is_protected
    app = Flask(__name__)
    # mutations on the newly-protected prefixes → protected
    for path in ["/api/jobs/123/cancel", "/api/tasks", "/api/scheduler/execute",
                 "/api/meta/cancel_job/1", "/api/progress-test/spawn", "/api/memory/5"]:
        for method in ["POST", "PUT", "DELETE", "PATCH"]:
            with app.test_request_context(path, method=method):
                assert _is_protected() is True, f"{method} {path} should be protected"
    # reads on the same prefixes stay public (local UI must keep working)
    for path in ["/api/jobs", "/api/tasks", "/api/scheduler/tasks"]:
        with app.test_request_context(path, method="GET"):
            assert _is_protected() is False, f"GET {path} should stay public"
    # an unrelated endpoint is unaffected
    with app.test_request_context("/api/health", method="POST"):
        assert _is_protected() is False


# ---- download_model whitelist ----------------------------------------------
def test_download_model_rejects_non_whitelisted(monkeypatch):
    import backend.api.batch_image_generation_api as m
    app = Flask(__name__)
    app.register_blueprint(m.batch_image_bp)
    monkeypatch.setattr(m, "service_available", True, raising=False)
    fake_imggen = MagicMock()
    fake_imggen.available_models = {"z-image-turbo": {}, "sdxl": {}}
    fake_gen = MagicMock()
    fake_gen.image_generator = fake_imggen
    monkeypatch.setattr(m, "get_batch_image_generator", lambda: fake_gen)

    client = app.test_client()
    bad = client.post("/api/batch-image/models/download", json={"model_path": "evil/arbitrary-hf-repo"})
    assert bad.status_code == 400, "arbitrary HF repo must be rejected"
