"""Deciding on a held push of changed files (/approvals/<id>/decide).

"partial" applies only the files the person ticked. With none ticked it must
apply nothing: it is refused with 400 and the push stays pending, instead of
falling back to every file in the push.
"""
import json

import pytest
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorPendingApproval, Setting, db
from backend.utils import auth_guard

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
FILES = [
    {"path": "backend/a.py", "content": "a = 1\n", "hash": "ha"},
    {"path": "backend/b.py", "content": "b = 2\n", "hash": "hb"},
]


class _FileSync:
    def __init__(self):
        self.applied = []

    def apply_files_atomic(self, files, strategy, create_backup=True):
        self.applied.append([f["path"] for f in files])
        return True, {"summary": {"total_processed": len(files), "total_updated": len(files)},
                      "details": [{"path": f["path"], "status": "success"} for f in files]}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    file_sync = _FileSync()
    monkeypatch.setattr(ic_api, "get_file_sync_service", lambda: file_sync)
    application = Flask(__name__)
    application.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(application)
    application.before_request(auth_guard.check_endpoint_auth)
    application.register_blueprint(ic_api.interconnector_bp)
    with application.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "client"})))
        approval = InterconnectorPendingApproval(
            push_id="push-1", source_node="master", sync_type="files",
            files_data=json.dumps(FILES), entities_data="{}", status="pending")
        db.session.add(approval)
        db.session.commit()
        application.approval_id = approval.id
        application.file_sync = file_sync
        yield application
        db.session.remove()
        db.drop_all()


def _decide(app, payload):
    return app.test_client().post(
        f"/api/interconnector/approvals/{app.approval_id}/decide", json=payload, environ_base=LOCAL)


def _status(app):
    with app.app_context():
        return db.session.get(InterconnectorPendingApproval, app.approval_id).status


@pytest.mark.parametrize("payload", [
    {"decision": "partial", "approved_files": []},
    {"decision": "partial", "approved_files": None},
    {"decision": "partial"},
])
def test_partial_with_nothing_ticked_applies_nothing(app, payload):
    response = _decide(app, payload)
    assert response.status_code == 400
    assert "Tick at least one file" in response.get_json()["error"]["message"]
    assert app.file_sync.applied == []
    assert _status(app) == "pending"


def test_partial_applies_only_the_ticked_files(app):
    response = _decide(app, {"decision": "partial", "approved_files": ["backend/b.py"]})
    assert response.status_code == 200
    assert app.file_sync.applied == [["backend/b.py"]]
    assert _status(app) == "approved"


def test_approve_applies_the_whole_push(app):
    assert _decide(app, {"decision": "approve"}).status_code == 200
    assert app.file_sync.applied == [["backend/a.py", "backend/b.py"]]


def test_decline_applies_nothing(app):
    assert _decide(app, {"decision": "decline"}).status_code == 200
    assert app.file_sync.applied == []
    assert _status(app) == "declined"


def test_a_decided_push_is_not_decided_again(app):
    assert _decide(app, {"decision": "partial", "approved_files": ["backend/a.py"]}).status_code == 200
    assert _decide(app, {"decision": "approve"}).status_code == 422
    assert app.file_sync.applied == [["backend/a.py"]]
