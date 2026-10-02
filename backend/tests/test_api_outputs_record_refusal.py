"""/api/outputs never serves consent records or dot-files, to any host.

Builds its own outputs tree in a temp folder and drives the real blueprint
behind the real auth hook through Flask's test client; no backend, GPU or
network.
"""

import json

import pytest
from flask import Flask

from backend.api import output_api
from backend.utils import auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    for rel in [
        "generated_images/cat.png",
        "generated_images/cat.png.consent",
        "edit_inputs/edit_src_1.png",
        "edit_inputs/edit_src_1.png.consent",
        "generated_animations/loop.gif",
    ]:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    tracking = root / "tracking"
    tracking.mkdir()
    (tracking / ".job_tracking_1.json").write_text(json.dumps({"metadata": {}}))
    (tracking / "job_tracking_2.json").write_text(json.dumps({"metadata": {}}))
    monkeypatch.setattr(output_api, "OUTPUT_DIR", str(root))
    monkeypatch.setattr(output_api, "TRACKING_DIR", str(tracking))
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(output_api.output_bp)
    return app.test_client()


def _get(client, path, addr):
    return client.get(f"/api/outputs/{path}", environ_base={"REMOTE_ADDR": addr})


@pytest.mark.parametrize("addr", [REMOTE, LOCAL])
@pytest.mark.parametrize("path", [
    "generated_images/cat.png",
    "edit_inputs/edit_src_1.png",
    "generated_animations/loop.gif",
    "job_tracking_2.json",
])
def test_outputs_stay_open_to_lan_browsers(client, path, addr):
    assert _get(client, path, addr).status_code == 200


@pytest.mark.parametrize("addr", [REMOTE, LOCAL])
@pytest.mark.parametrize("path", [
    "generated_images/cat.png.consent",
    "edit_inputs/edit_src_1.png.consent",
    "edit_inputs/EDIT_SRC_1.PNG.CONSENT",
    # secure_filename turns these into edit_src_1.png.consent.
    "edit_inputs/edit_src_1.png.consent.",
    "edit_inputs/edit_src_1.png.consent_",
    ".job_tracking_1.json",
    ".job_tracking_1.json/csv",
])
def test_consent_records_and_dot_files_are_refused(client, path, addr):
    assert _get(client, path, addr).status_code == 404


def test_refused_name():
    assert output_api._refused_name("a.png.consent")
    assert output_api._refused_name(".env")
    assert not output_api._refused_name("a.png")
    assert not output_api._refused_name("consent_form.png")
