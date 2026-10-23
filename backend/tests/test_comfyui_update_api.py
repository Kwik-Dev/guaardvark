"""The ComfyUI update routes: payload shapes and the 202/409 split, over a
service whose job never runs (the update service is tested on its own)."""

import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.api import plugins_api
from backend.services import comfyui_update_service as cus

CHECK = {
    "installed": "0.33.0", "target": "0.39.0", "available": True, "busy": None,
    "disk_free_gb": 120.5, "package_changes": [{"name": "comfy-kitchen", "from": "0.2.31", "to": "0.2.37"}],
    "previous": None, "keeps": ["models", "custom_nodes"], "download": "Downloads ... GitHub ... PyPI.",
}


class FakeService:
    def __init__(self):
        self.refuse = None
        self.started = []
        self.repair_needed = None

    def check(self, target=None):
        return dict(CHECK, needs_repair=self.repair_needed)

    def status(self):
        s = cus.get_status()
        if self.repair_needed and s["state"] != "running":
            s["state"] = "needs_repair"
            s["message"] = self.repair_needed
        return s

    def repair(self, *, background=True):
        if self.refuse:
            raise cus.UpdateRefused(self.refuse)
        self.started.append(("repair", None))
        return {"state": "running", "phase": "swap"}

    def start(self, target=None, *, background=True):
        if self.refuse:
            raise cus.UpdateRefused(self.refuse)
        self.started.append(("update", target))
        return {"state": "running", "phase": "fetch"}

    def restore_previous(self, *, background=True):
        if self.refuse:
            raise cus.UpdateRefused(self.refuse)
        self.started.append(("restore", None))
        return {"state": "running", "phase": "snapshot"}


@pytest.fixture
def client(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(cus, "get_update_service", lambda: fake)
    cus.reset_status()
    app = Flask(__name__)
    app.register_blueprint(plugins_api.plugins_bp)
    c = app.test_client()
    c.fake = fake
    yield c
    cus.reset_status()


def test_check_returns_the_dialog_payload(client):
    r = client.get("/api/plugins/comfyui/update/check")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["data"] == dict(CHECK, needs_repair=None)


def test_update_starts_with_202(client):
    r = client.post("/api/plugins/comfyui/update", json={"target": "v0.39.0"})
    assert r.status_code == 202
    assert r.get_json()["data"]["state"] == "running"
    assert client.fake.started == [("update", "v0.39.0")]


def test_update_refusal_is_409(client):
    client.fake.refuse = "ComfyUI is busy: 1 active video render job(s) in progress. Try again when it is idle."
    r = client.post("/api/plugins/comfyui/update")
    assert r.status_code == 409
    body = r.get_json()
    assert body["success"] is False
    assert body["error"]["message"] == client.fake.refuse
    assert client.fake.started == []


def test_status_has_the_agreed_fields(client):
    r = client.get("/api/plugins/comfyui/update/status")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert set(data) == {"state", "phase", "phase_label", "percent", "from", "to",
                         "started_at", "finished_at", "message", "log_tail"}
    assert data["state"] == "idle" and data["log_tail"] == []


def test_restore_previous_202_and_409(client):
    r = client.post("/api/plugins/comfyui/update/restore-previous")
    assert r.status_code == 202
    assert client.fake.started == [("restore", None)]
    client.fake.refuse = "There is no previous ComfyUI tree to restore."
    r = client.post("/api/plugins/comfyui/update/restore-previous")
    assert r.status_code == 409
    assert "no previous" in r.get_json()["error"]["message"]


def test_status_reports_needs_repair(client):
    client.fake.repair_needed = "A ComfyUI update was interrupted at swap: old tree at .../ComfyUI.prev-0.33.0"
    data = client.get("/api/plugins/comfyui/update/status").get_json()["data"]
    assert data["state"] == "needs_repair"
    assert data["message"] == client.fake.repair_needed
    assert set(data) == {"state", "phase", "phase_label", "percent", "from", "to",
                         "started_at", "finished_at", "message", "log_tail"}
    assert client.get("/api/plugins/comfyui/update/check").get_json()["data"]["needs_repair"] == client.fake.repair_needed


def test_repair_202_and_409(client):
    r = client.post("/api/plugins/comfyui/update/repair")
    assert r.status_code == 202
    assert r.get_json()["data"]["state"] == "running"
    assert client.fake.started == [("repair", None)]
    client.fake.refuse = "There is nothing to repair."
    r = client.post("/api/plugins/comfyui/update/repair")
    assert r.status_code == 409
    assert r.get_json()["error"] == {"code": "COMFYUI_UPDATE_REFUSED", "message": "There is nothing to repair."}
