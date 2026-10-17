"""Deciding on a held push (/approvals/<id>/decide).

"partial" applies only the files the person ticked. With none ticked it must
apply nothing: it is refused with 400 and the push stays pending, instead of
falling back to every file in the push.

Approved files replace the local copy even when it is newer (remote_wins,
which backs it up first). A held entity change, queued by the sync service
when a client pushed a row the master also has, applies the client's version
when approved. Whenever nothing could be applied the approval stays pending.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timedelta

import pytest
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import Client, InterconnectorPendingApproval, Setting, db
from backend.services import interconnector_file_sync_service as file_sync_mod
from backend.utils import auth_guard

LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
FILES = [
    {"path": "backend/a.py", "content": "a = 1\n", "hash": "ha"},
    {"path": "backend/b.py", "content": "b = 2\n", "hash": "hb"},
]


class _FileSync:
    def __init__(self):
        self.applied = []
        self.strategies = []
        self.fail_on = None

    def apply_files_atomic(self, files, strategy, create_backup=True):
        self.applied.append([f["path"] for f in files])
        self.strategies.append(strategy)
        if self.fail_on:
            return False, {"summary": {"total_errors": 1, "rolled_back": True},
                           "details": [{"path": self.fail_on, "status": "error", "error": "disk full"}]}
        return True, {"summary": {"total_processed": len(files), "total_updated": len(files)},
                      "details": [{"path": f["path"], "status": "success", "updated": True} for f in files]}


def _make_app(monkeypatch, file_sync):
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.delenv(auth_guard.NETWORK_ACCESS_ENV, raising=False)
    monkeypatch.setattr(ic_api, "get_file_sync_service", lambda: file_sync)
    application = Flask(__name__)
    application.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(application)
    application.before_request(auth_guard.check_endpoint_auth)
    application.register_blueprint(ic_api.interconnector_bp)
    return application


@pytest.fixture
def app(monkeypatch):
    file_sync = _FileSync()
    application = _make_app(monkeypatch, file_sync)
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


def _decide(app, payload, approval_id=None):
    return app.test_client().post(
        f"/api/interconnector/approvals/{approval_id or app.approval_id}/decide",
        json=payload, environ_base=LOCAL)


def _status(app, approval_id=None):
    with app.app_context():
        return db.session.get(InterconnectorPendingApproval, approval_id or app.approval_id).status


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


def test_approved_files_are_applied_as_the_person_chose_them(app):
    assert _decide(app, {"decision": "approve"}).status_code == 200
    assert app.file_sync.strategies == ["remote_wins"]


def test_a_failed_apply_keeps_the_push_pending_and_says_which_file(app):
    app.file_sync.fail_on = "backend/b.py"
    response = _decide(app, {"decision": "approve"})
    assert response.status_code == 409
    body = response.get_json()
    assert "backend/b.py" in body["error"]["message"]
    assert "stays pending" in body["error"]["message"]
    assert _status(app) == "pending"


def test_decline_applies_nothing(app):
    assert _decide(app, {"decision": "decline"}).status_code == 200
    assert app.file_sync.applied == []
    assert _status(app) == "declined"


def test_a_decided_push_is_not_decided_again(app):
    assert _decide(app, {"decision": "partial", "approved_files": ["backend/a.py"]}).status_code == 200
    assert _decide(app, {"decision": "approve"}).status_code == 422
    assert app.file_sync.applied == [["backend/a.py"]]


# ---- the real file sync service: a newer local copy is still replaced ---------

@pytest.fixture
def real_files_app(monkeypatch, tmp_path):
    monkeypatch.setattr(file_sync_mod, "_SENTINEL_DIR", tmp_path / "sentinel")
    monkeypatch.setattr(file_sync_mod, "_SENTINEL_FILE", tmp_path / "sentinel" / ".sync_in_progress")
    service = file_sync_mod.InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(service, "_record_update_applied", lambda root, written: None)
    application = _make_app(monkeypatch, service)

    local = tmp_path / "backend" / "a.py"
    local.parent.mkdir(parents=True)
    local.write_text("a = 'edited here'\n")
    # The local copy is newer than the held one.
    os.utime(local, (time.time(), time.time()))
    held = "a = 'from the master'\n"
    files = [{
        "path": "backend/a.py",
        "content": held,
        "hash": hashlib.sha256(held.encode()).hexdigest(),
        "modified_at": (datetime.now() - timedelta(days=1)).isoformat(),
    }]
    with application.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "client"})))
        approval = InterconnectorPendingApproval(
            push_id="push-2", source_node="master", sync_type="files",
            files_data=json.dumps(files), entities_data="{}", status="pending")
        db.session.add(approval)
        db.session.commit()
        application.approval_id = approval.id
        application.root = tmp_path
        application.held = held
        yield application
        db.session.remove()
        db.drop_all()


def test_an_approved_file_replaces_a_newer_local_copy_and_keeps_a_backup(real_files_app):
    response = _decide(real_files_app, {"decision": "approve"})

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()["data"]
    assert body["apply_results"]["updated"] == 1
    assert body["details"][0]["path"] == "backend/a.py"
    assert body["details"][0]["updated"] is True
    assert (real_files_app.root / "backend" / "a.py").read_text() == real_files_app.held
    backups = list((real_files_app.root / "backups" / "file_sync").glob("backend_a.py.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "a = 'edited here'\n"
    assert _status(real_files_app) == "approved"


# ---- held entity changes ------------------------------------------------------

@pytest.fixture
def entity_app(monkeypatch):
    application = _make_app(monkeypatch, _FileSync())
    with application.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master"})))
        # Edited on the master after the client's push was held.
        master_row = Client(id=7, name="Acme", notes="master notes",
                            updated_at=datetime.now())
        db.session.add(master_row)
        db.session.commit()
        client_version = {
            "id": 7,
            "name": "Acme",
            "notes": "client notes",
            "updated_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        }
        approval = InterconnectorPendingApproval(
            push_id="push-3", source_node="client-node", sync_type="entities",
            entities_data=json.dumps({"entity_type": "clients", "entity_id": 7,
                                      "client_data": client_version, "master_data": {}}),
            status="pending")
        empty = InterconnectorPendingApproval(
            push_id="push-4", source_node="client-node", sync_type="entities",
            entities_data=json.dumps({}), status="pending")
        db.session.add_all([approval, empty])
        db.session.commit()
        application.approval_id = approval.id
        application.empty_id = empty.id
        yield application
        db.session.remove()
        db.drop_all()


def _notes(app):
    with app.app_context():
        db.session.expire_all()
        return db.session.get(Client, 7).notes


def test_approving_an_entity_change_applies_the_clients_version(entity_app):
    response = _decide(entity_app, {"decision": "approve"})

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.get_json()["data"]["apply_results"]["updated"] == 1
    assert _notes(entity_app) == "client notes"
    assert _status(entity_app) == "approved"


def test_declining_an_entity_change_leaves_the_row(entity_app):
    assert _decide(entity_app, {"decision": "decline"}).status_code == 200
    assert _notes(entity_app) == "master notes"
    assert _status(entity_app) == "declined"


def test_an_entity_change_is_not_approved_in_part(entity_app):
    response = _decide(entity_app, {"decision": "partial", "approved_files": ["x"]})
    assert response.status_code == 422
    assert _notes(entity_app) == "master notes"
    assert _status(entity_app) == "pending"


def test_an_entity_approval_with_nothing_to_apply_stays_pending(entity_app):
    response = _decide(entity_app, {"decision": "approve"}, approval_id=entity_app.empty_id)
    assert response.status_code == 409
    assert "stays pending" in response.get_json()["error"]["message"]
    assert _status(entity_app, entity_app.empty_id) == "pending"
