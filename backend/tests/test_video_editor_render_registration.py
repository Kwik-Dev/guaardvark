"""HTTP-layer regression tests for /api/video-editor/shotcut/compose-arrangement.

The plugin registers a finished render as a Document by POSTing back to
/api/outputs/register from its OWN process, and that POST is non-fatal by
design. So a wrong backend port, an unreachable backend or a timeout used to
return a body with `rendered_mp4` but no `rendered_mp4_doc_id` — which the
Video Editor frontend needs to address the video (preview + download). The
render existed on disk and was simply unaddressable, with nothing said.

Observed 2026-08-30: plugins/video_editor/config.yaml pointed
registration.backend_url at :5002 while the backend ran on :5055.

The proxy must register the render itself instead of returning a dead body.
"""
from __future__ import annotations

import pytest
from flask import Flask

from backend.api import video_editor_api as vea


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(vea, "_resolve_document", lambda d: None)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vea.video_editor_bp)
    return app.test_client()


class _Doc:
    id = 4242


def _proxy(body):
    """A _proxy_post stand-in that never touches the plugin."""
    return lambda path, payload, timeout=None, **k: (body, 200)


def test_registers_the_render_when_the_plugin_did_not(client, monkeypatch, tmp_path):
    final = tmp_path / "arrangement_abc.mp4"
    final.write_bytes(b"final-bytes")

    monkeypatch.setattr(
        vea, "_proxy_post",
        _proxy({"rendered_mp4": str(final), "mlt_path": "/out/x.mlt", "documents": []}),
    )

    calls = {}

    def fake_register(physical_path, folder_name=None, file_metadata=None, **kw):
        calls["path"] = physical_path
        calls["folder"] = folder_name
        calls["meta"] = file_metadata
        return _Doc()

    import backend.services.output_registration as reg
    monkeypatch.setattr(reg, "register_file", fake_register)

    r = client.post("/api/video-editor/shotcut/compose-arrangement", json={"arrangement": {"clips": [{"clip_id": "a"}]}})
    assert r.status_code == 200
    body = r.get_json()

    assert calls["path"] == str(final)
    assert calls["folder"] == "Videos"
    assert calls["meta"]["fallback_registration"] is True
    assert body["rendered_mp4_doc_id"] == 4242


def test_prefers_the_plugin_document_when_present(client, monkeypatch, tmp_path):
    final = tmp_path / "arrangement_abc.mp4"
    final.write_bytes(b"final-bytes")

    monkeypatch.setattr(
        vea, "_proxy_post",
        _proxy({
            "rendered_mp4": str(final),
            "mlt_path": "/out/x.mlt",
            "documents": [{"id": 77, "path": "Videos/x.mp4", "filename": "x.mp4"}],
        }),
    )

    import backend.services.output_registration as reg
    monkeypatch.setattr(
        reg, "register_file",
        lambda *a, **k: pytest.fail("fallback must not run when the plugin registered"),
    )

    r = client.post("/api/video-editor/shotcut/compose-arrangement", json={"arrangement": {"clips": [{"clip_id": "a"}]}})
    assert r.get_json()["rendered_mp4_doc_id"] == 77


def test_reports_no_doc_id_when_registration_itself_fails(client, monkeypatch, tmp_path):
    """Registration failing must not 500 the render — but it must not promise an
    id it does not have either."""
    final = tmp_path / "arrangement_abc.mp4"
    final.write_bytes(b"final-bytes")

    monkeypatch.setattr(
        vea, "_proxy_post",
        _proxy({"rendered_mp4": str(final), "mlt_path": "/out/x.mlt", "documents": []}),
    )

    import backend.services.output_registration as reg
    monkeypatch.setattr(reg, "register_file", lambda *a, **k: None)

    r = client.post("/api/video-editor/shotcut/compose-arrangement", json={"arrangement": {"clips": [{"clip_id": "a"}]}})
    assert r.status_code == 200
    assert r.get_json().get("rendered_mp4_doc_id") is None


def test_does_not_register_a_missing_render_file(client, monkeypatch, tmp_path):
    """No file on disk means nothing to register — stay quiet, do not invent a doc."""
    monkeypatch.setattr(
        vea, "_proxy_post",
        _proxy({"rendered_mp4": str(tmp_path / "gone.mp4"), "mlt_path": "/out/x.mlt", "documents": []}),
    )

    import backend.services.output_registration as reg
    monkeypatch.setattr(
        reg, "register_file",
        lambda *a, **k: pytest.fail("must not register a path that does not exist"),
    )

    r = client.post("/api/video-editor/shotcut/compose-arrangement", json={"arrangement": {"clips": [{"clip_id": "a"}]}})
    assert r.status_code == 200
    assert r.get_json().get("rendered_mp4_doc_id") is None
