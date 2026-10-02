"""The backend answers only requests addressed to one of this install's names
(backend/utils/host_check.py), which is what stops a page whose DNS name was
re-pointed at this machine (DNS rebinding).

An app is built the way backend/app.py builds it: init_cors, Socket.IO's
middleware, then HostCheckMiddleware around both, with a recorder view. The
Host headers are the ones each real caller sends. The machine's names are a
fixed stand-in; no backend, GPU or network.
"""

from pathlib import Path

import pytest
from flask import Flask
from flask_socketio import SocketIO

from backend.utils import cors_policy
from backend.utils.host_check import HOST_CODE, HostCheckMiddleware, host_allowed, host_name

MACHINE = frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.20", "workstation", "workstation.local"})
ENV_NAMES = (
    "VITE_PORT", "FLASK_PORT", "PORT", "VITE_FRONTEND_URL", "VITE_ALLOWED_HOSTS",
    "VITE_API_BASE_URL", "VITE_SOCKET_URL", cors_policy.EXTRA_ORIGINS_ENV,
)
REBOUND = {"Host": "evil.example:5000", "Origin": "http://evil.example:5000"}


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    monkeypatch.setattr(cors_policy, "_machine_hosts", lambda: MACHINE)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def app_client():
    ran = []
    app = Flask(__name__)

    @app.route("/api/thing", methods=["GET", "POST"])
    def thing():
        ran.append("thing")
        return {"ok": True}

    cors_policy.init_cors(app)
    SocketIO(app, async_mode="threading", cors_allowed_origins=cors_policy.socketio_origin_allowed)
    app.wsgi_app = HostCheckMiddleware(app.wsgi_app)
    return app.test_client(), ran


# ---- callers that must keep working ----------------------------------------

@pytest.mark.parametrize("host", [
    "127.0.0.1:5000",       # the web UI through Vite's proxy (changeOrigin), the MCP server
    "localhost:5000",       # the CLI, sidecars calling back, Docker's health check
    "localhost",            # Flask's test client
    "[::1]:5000",
    "192.168.1.20",         # Docker's nginx passing a browser's Host, no port
    "10.0.0.7:5000",        # any IP: an Interconnector or cluster node, a new DHCP lease, NAT
    "[fe80::1]:5000",
    "workstation:5000",     # this machine's hostname, first label and .local name
    "WORKSTATION.local:5000",
    "localhost.:5000",
])
def test_this_machine_and_any_ip_address_are_answered(host):
    assert host_allowed(host)


def test_a_request_without_a_host_header_is_answered():
    assert host_allowed(None)


@pytest.mark.parametrize("host", [
    "evil.example",
    "evil.example:5000",
    "workstation.evil.example",
    "localhost.evil.example",
    "127.0.0.1.nip.io:5000",
    "backend:5000",
    "",
    "evil example",
    "evil.example:http",
    "[::1:5000",
    "[not-an-ip]:5000",
])
def test_other_names_are_refused(host):
    assert not host_allowed(host)


@pytest.mark.parametrize("variable, value, host", [
    ("VITE_ALLOWED_HOSTS", "gpubox.lan", "gpubox.lan:5000"),
    ("VITE_ALLOWED_HOSTS", "backend", "backend:5000"),          # docker-compose.yml's line
    ("VITE_ALLOWED_HOSTS", ".home.lan", "gpubox.home.lan"),
    ("VITE_ALLOWED_HOSTS", ".home.lan", "home.lan:5000"),
    ("GUAARDVARK_CORS_ORIGINS", "https://guaardvark.example", "guaardvark.example"),
    ("GUAARDVARK_CORS_ORIGINS", "http://myhost:5173", "myhost"),
    ("VITE_FRONTEND_URL", "https://ui.example.lan", "ui.example.lan"),
    ("VITE_API_BASE_URL", "http://api.example.lan:5000/api", "api.example.lan:5000"),
    ("VITE_SOCKET_URL", "http://api.example.lan:5000", "api.example.lan:5000"),
])
def test_configured_names_are_answered(monkeypatch, variable, value, host):
    assert not host_allowed(host)
    monkeypatch.setenv(variable, value)
    assert host_allowed(host)


def test_a_suffix_entry_does_not_match_a_longer_name_that_only_ends_the_same(monkeypatch):
    monkeypatch.setenv("VITE_ALLOWED_HOSTS", ".home.lan")
    assert not host_allowed("evilhome.lan")


def test_a_relative_api_base_adds_no_name(monkeypatch):
    monkeypatch.setenv("VITE_API_BASE_URL", "/api")
    assert not host_allowed("api")


def test_vite_allowed_hosts_all_turns_the_check_off(monkeypatch):
    monkeypatch.setenv("VITE_ALLOWED_HOSTS", "all")
    assert host_allowed("evil.example:5000")


def test_host_name_normalizes():
    assert host_name("WorkStation.Local.:5000") == "workstation.local"
    assert host_name("[::1]:5000") == "[::1]"
    assert host_name("[::1]x") is None


# ---- the middleware ---------------------------------------------------------

def test_the_ui_through_vites_proxy_reaches_the_view(app_client):
    client, ran = app_client
    response = client.post("/api/thing", headers={"Host": "127.0.0.1:5000", "X-Forwarded-Host": "localhost:5173"})
    assert response.status_code == 200
    assert ran == ["thing"]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_rebound_page_is_refused_before_any_view(app_client, method):
    client, ran = app_client
    response = client.open("/api/thing", method=method, headers=REBOUND)
    assert response.status_code == 421
    body = response.get_json()
    assert body["code"] == HOST_CODE
    assert "evil.example" in body["error"] and cors_policy.EXTRA_ORIGINS_ENV in body["error"]
    assert ran == []


def test_a_rebound_page_cannot_open_socket_io(app_client):
    client, _ = app_client
    path = "/socket.io/?EIO=4&transport=polling"
    assert client.get(path, headers={"Host": "localhost:5000"}).status_code == 200
    assert client.get(path, headers={"Host": "evil.example:5000"}).status_code == 421


def test_a_forged_forwarded_host_does_not_help(app_client):
    client, ran = app_client
    response = client.post("/api/thing", headers={**REBOUND, "X-Forwarded-Host": "localhost:5173"})
    assert response.status_code == 421
    assert ran == []


def test_the_refusal_names_the_address_the_browser_used(app_client):
    # Docker's nginx sends Host without the port and X-Forwarded-Host with it.
    client, _ = app_client
    response = client.get("/api/thing", headers={"Host": "myhost", "X-Forwarded-Host": "myhost:5173"})
    assert response.status_code == 421
    assert "http://myhost:5173" in response.get_json()["error"]


def test_backend_app_wraps_socket_io_too():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    wrap = source.index("app.wsgi_app = HostCheckMiddleware(app.wsgi_app)")
    assert 0 < source.index("socketio.init_app(app)") < wrap
