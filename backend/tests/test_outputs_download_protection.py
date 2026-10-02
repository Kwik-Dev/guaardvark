"""GET /outputs/<path> answers only localhost unless the API key is sent.

Builds its own outputs tree in a temp folder and drives the real blueprint
behind the real auth hook through Flask's test client; no backend, GPU or
network.
"""

import pytest
from flask import Flask

from backend.routes.download_route import download_bp
from backend.utils import auth_guard

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"

FILES = [
    "generated_images/cat.png",
    "csv/table.csv",
    "chat-exports/chats-20260101-000000/index.json",
    "screenshots/agent_capture_1.webp",
    "consent/abc123.consent",
    "training/demo/s00.wav",
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "outputs"
    for rel in FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    # Pin the machine's own addresses so no interface probe runs.
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    app = Flask(__name__)
    app.config["OUTPUT_DIR"] = str(root)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(download_bp)
    return app.test_client()


def _get(client, rel, addr, headers=None):
    return client.get(f"/outputs/{rel}", environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


@pytest.mark.parametrize("rel", FILES)
def test_every_file_is_refused_to_other_hosts_without_a_key(client, rel):
    assert _get(client, rel, REMOTE).status_code == 403


@pytest.mark.parametrize("rel", FILES)
def test_every_file_is_served_to_localhost_without_a_key(client, rel):
    assert _get(client, rel, LOCAL).status_code == 200


def test_a_missing_file_is_refused_not_reported_missing(client):
    # A 404 here would let another host enumerate export timestamps.
    assert _get(client, "chat-exports/chats-20990101-000000/index.json", REMOTE).status_code == 403
    assert _get(client, "generated_images/missing.png", LOCAL).status_code == 404


@pytest.mark.parametrize("rel", [
    "generated_images/%2E%2E/chat-exports/chats-20260101-000000/index.json",
    "chat%2Dexports/chats-20260101-000000/index.json",
    "./screenshots/agent_capture_1.webp",
])
def test_encoded_or_dotted_paths_are_refused_to_other_hosts(client, rel):
    assert _get(client, rel, REMOTE).status_code == 403


def test_a_lan_device_through_the_local_proxy_counts_as_remote(client):
    response = _get(client, FILES[0], LOCAL, headers={"X-Forwarded-For": REMOTE})
    assert response.status_code == 403


def test_with_an_api_key_set_every_host_needs_it(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    for rel in (FILES[0], FILES[2]):
        assert _get(client, rel, REMOTE).status_code == 401
        assert _get(client, rel, REMOTE, headers={"X-API-Key": "wrong"}).status_code == 401
        assert _get(client, rel, REMOTE, headers={"X-API-Key": "k-test"}).status_code == 200
        assert _get(client, rel, LOCAL).status_code == 401
        assert _get(client, rel, LOCAL, headers={"X-API-Key": "k-test"}).status_code == 200


def test_the_api_outputs_prefix_is_not_caught_by_the_download_rule():
    # The web UI loads outputs through /api/outputs from LAN browsers.
    with Flask(__name__).test_request_context("/api/outputs/generated_images/a.png"):
        assert auth_guard._is_protected() is False
