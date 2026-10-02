"""Which pages may call the backend from another origin (backend/utils/cors_policy.py).

Only this install's own pages: its frontend and backend ports on this
machine's names and addresses, VITE_FRONTEND_URL, and GUAARDVARK_CORS_ORIGINS.
The Interconnector's three node routes also take private-network and loopback
origins. Flask-CORS is driven through init_cors (what backend/app.py calls) on
a bare Flask app, Engine.IO through a real Socket.IO server using the same
check, and the sign-in cookie through api_session. Preflights to protected
routes go through the real auth hook and tools blueprint with a stand-in tool
registry. The machine's names are a fixed stand-in; no backend, GPU or network.
"""

from types import SimpleNamespace

import pytest
import socketio as python_socketio
from flask import Flask, request
from werkzeug.test import Client

from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolResult
from backend.utils import api_session, auth_guard, cors_policy

MACHINE = frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.20", "workstation", "workstation.local"})
OTHER_DEVICE = "http://192.168.1.77:5173"
REMOTE = {"REMOTE_ADDR": "192.0.2.10"}  # TEST-NET-1: never one of this machine's addresses
ENV_NAMES = (
    "VITE_PORT", "FLASK_PORT", "PORT", "VITE_FRONTEND_URL", "VITE_ALLOWED_HOSTS",
    cors_policy.EXTRA_ORIGINS_ENV, auth_guard.API_KEY_ENV, auth_guard.TOOL_ENDPOINTS_ENV,
)


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    monkeypatch.setattr(cors_policy, "_machine_hosts", lambda: MACHINE)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _app():
    app = Flask(__name__)
    app.url_map.strict_slashes = False

    @app.route("/api/settings/x", methods=["GET", "POST", "PATCH"])
    def settings_x():
        return {"ok": True}

    from backend.api.interconnector_api import interconnector_bp

    app.register_blueprint(interconnector_bp)
    cors_policy.init_cors(app)
    return app


@pytest.fixture
def client():
    return _app().test_client()


def _allow_origin(response):
    return response.headers.get("Access-Control-Allow-Origin")


def _preflight(client, path, origin, method="POST"):
    return client.options(path, headers={
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type,x-api-key",
    })


# ---- the list ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("http://localhost:5173", "http://localhost:5173"),
    ("HTTP://LocalHost:5173/", "http://localhost:5173"),
    ("https://gv.example.com:443/app", "https://gv.example.com"),
    ("http://gv.example.com:80", "http://gv.example.com"),
    ("http://[::1]:5173", "http://[::1]:5173"),
    ("null", None),
    ("file:///home/x.html", None),
    ("http://host:notaport", None),
    ("", None),
    (None, None),
])
def test_origins_are_compared_as_a_browser_writes_them(value, expected):
    assert cors_policy.normalize_origin(value) == expected


def test_every_name_of_this_machine_is_allowed_at_its_own_ports():
    allowed = set(cors_policy.allowed_origins())
    for host in ("localhost", "127.0.0.1", "[::1]", "192.168.1.20", "workstation", "workstation.local"):
        for scheme in ("http", "https"):
            assert f"{scheme}://{host}:5173" in allowed
            assert f"{scheme}://{host}:5000" in allowed


@pytest.mark.parametrize("origin", [
    OTHER_DEVICE,
    "http://10.0.0.9:8080",
    "http://localhost:3000",
    "http://localhost:5175",
    "http://127.0.0.1:8188",
    "http://workstation:8080",
    "https://evil.example",
    "null",
])
def test_other_devices_and_other_ports_are_not(origin):
    assert not cors_policy.origin_allowed(origin)


def test_relocated_ports_replace_the_defaults(monkeypatch):
    monkeypatch.setenv("VITE_PORT", "5273")
    monkeypatch.setenv("FLASK_PORT", "5100")
    assert cors_policy.origin_allowed("http://localhost:5273")
    assert cors_policy.origin_allowed("http://192.168.1.20:5100")
    assert not cors_policy.origin_allowed("http://localhost:5173")
    assert not cors_policy.origin_allowed("http://localhost:5000")


def test_port_is_the_backend_port_when_flask_port_is_unset(monkeypatch):
    monkeypatch.setenv("PORT", "5400")
    assert cors_policy.origin_allowed("http://127.0.0.1:5400")


def test_frontend_url_and_allowed_hosts_are_added(monkeypatch):
    monkeypatch.setenv("VITE_FRONTEND_URL", "HTTPS://Gv.Example.com:443/app/")
    monkeypatch.setenv("VITE_ALLOWED_HOSTS", "box.lan, all, .local")
    assert cors_policy.origin_allowed("https://gv.example.com")
    assert cors_policy.origin_allowed("http://box.lan:5173")
    # "all" and ".local" widen Vite's Host check; they name no machine.
    assert not cors_policy.origin_allowed("http://printer.local:5173")
    assert not cors_policy.origin_allowed("http://all:5173")


def test_extra_origins_are_an_explicit_opt_in(monkeypatch):
    monkeypatch.setenv(cors_policy.EXTRA_ORIGINS_ENV, "https://gv.example.com/, http://192.168.1.77:8080, *, nonsense")
    assert cors_policy.extra_origins() == ["https://gv.example.com", "http://192.168.1.77:8080"]
    assert cors_policy.origin_allowed("https://gv.example.com")
    assert cors_policy.origin_allowed("http://192.168.1.77:8080")
    assert not cors_policy.origin_allowed("https://evil.example")


def test_the_frontend_list_leaves_out_the_backend_port():
    frontend = cors_policy.frontend_origins()
    assert "http://192.168.1.20:5173" in frontend
    assert "http://192.168.1.20:5000" not in frontend


# ---- Flask-CORS -------------------------------------------------------------

def test_this_installs_frontend_reads_replies_with_its_sign_in(client):
    response = client.get("/api/settings/x", headers={"Origin": "http://192.168.1.20:5173"})
    assert _allow_origin(response) == "http://192.168.1.20:5173"
    assert response.headers.get("Access-Control-Allow-Credentials") == "true"
    assert "Origin" in " ".join(response.headers.getlist("Vary"))


@pytest.mark.parametrize("origin", [OTHER_DEVICE, "http://localhost:3000", "https://evil.example", "null"])
def test_other_pages_get_no_cors_headers(client, origin):
    response = client.get("/api/settings/x", headers={"Origin": origin})
    assert response.status_code == 200
    assert _allow_origin(response) is None
    assert response.headers.get("Access-Control-Allow-Credentials") is None


def test_a_reply_without_origin_carries_no_cors_headers(client):
    assert client.get("/api/settings/x").headers.getlist("Access-Control-Allow-Origin") == []


def test_preflight_allows_patch_and_the_api_key_header(client):
    response = _preflight(client, "/api/settings/x", "http://localhost:5173", method="PATCH")
    assert _allow_origin(response) == "http://localhost:5173"
    assert "PATCH" in response.headers["Access-Control-Allow-Methods"]
    assert "x-api-key" in response.headers["Access-Control-Allow-Headers"].lower()
    assert _allow_origin(_preflight(client, "/api/settings/x", OTHER_DEVICE)) is None


def test_the_node_route_pattern_covers_exactly_the_three_browser_called_routes(client):
    import re

    pattern = re.compile(rf"^{cors_policy.NODE_ROUTES}\Z")
    rules = {r.rule for r in client.application.url_map.iter_rules() if r.rule.startswith("/api/interconnector/")}
    matched = {rule for rule in rules if pattern.match(rule.replace("<node_id>", "n1"))}
    assert matched == {
        "/api/interconnector/status",
        "/api/interconnector/nodes/register",
        "/api/interconnector/nodes/<node_id>/heartbeat",
    }


@pytest.mark.parametrize("path", [
    "/api/interconnector/status",
    "/api/interconnector/status/",
    "/api/interconnector/nodes/register",
    "/api/interconnector/nodes/n1/heartbeat",
])
@pytest.mark.parametrize("origin", [OTHER_DEVICE, "http://10.0.0.9:5175", "http://172.20.1.2:3000", "http://localhost:5175"])
def test_a_client_nodes_ui_reaches_its_masters_node_routes(client, path, origin):
    assert _allow_origin(_preflight(client, path, origin)) == origin


@pytest.mark.parametrize("origin", ["https://evil.example", "http://100.64.1.2:5173", "http://192.168.1.77.evil.example"])
def test_node_routes_refuse_origins_off_the_private_network(client, origin):
    assert _allow_origin(_preflight(client, "/api/interconnector/nodes/register", origin)) is None


@pytest.mark.parametrize("path", ["/api/interconnector/config", "/api/interconnector/nodes", "/api/interconnector/nodes/n1"])
def test_other_interconnector_routes_answer_only_this_install(client, path):
    assert _allow_origin(_preflight(client, path, OTHER_DEVICE, method="DELETE")) is None
    assert _allow_origin(_preflight(client, path, "http://localhost:5173", method="DELETE")) == "http://localhost:5173"


def test_the_opt_in_reaches_flask_cors(monkeypatch):
    monkeypatch.setenv(cors_policy.EXTRA_ORIGINS_ENV, "https://gv.example.com")
    response = _app().test_client().get("/api/settings/x", headers={"Origin": "https://gv.example.com"})
    assert _allow_origin(response) == "https://gv.example.com"


# ---- Socket.IO --------------------------------------------------------------

def test_socketio_uses_the_same_check():
    from backend.socketio_instance import socketio

    assert socketio.server_options["cors_allowed_origins"] is cors_policy.socketio_origin_allowed


def _handshake(origin):
    server = python_socketio.Server(async_mode="threading", cors_allowed_origins=cors_policy.socketio_origin_allowed)
    headers = {"Origin": origin} if origin else {}
    return Client(python_socketio.WSGIApp(server)).get("/socket.io/?EIO=4&transport=polling", headers=headers)


@pytest.mark.parametrize("origin", [
    "http://localhost:5173",       # the web UI through the dev/preview proxy
    "http://192.168.1.20:5173",    # the web UI opened from another device
    "http://127.0.0.1:5000",       # the CLI's websocket, which names the backend
    None,                          # the CLI's polling requests
])
def test_engineio_accepts_this_install(origin):
    assert _handshake(origin).status_code == 200


@pytest.mark.parametrize("origin", [OTHER_DEVICE, "http://localhost:3000", "https://evil.example"])
def test_engineio_refuses_other_pages(origin):
    response = _handshake(origin)
    assert response.status_code == 400
    assert "Not an accepted origin" in response.get_data(as_text=True)


# ---- the sign-in cookie -----------------------------------------------------

@pytest.mark.parametrize("site,origin,accepted", [
    ("same-site", "http://192.168.1.20:5173", True),
    ("same-site", "http://workstation.local:5173", True),
    ("same-site", "http://192.168.1.20:5000", False),
    ("same-site", "http://192.168.1.20:8080", False),
    ("cross-site", "https://evil.example", False),
    ("same-origin", None, True),
])
def test_the_cookie_is_accepted_from_this_installs_frontend_only(site, origin, accepted):
    headers = {"Sec-Fetch-Site": site}
    if origin:
        headers["Origin"] = origin
    with Flask(__name__).test_request_context("/api/tools/execute", method="POST", headers=headers):
        assert api_session.request_origin_allowed() is accepted


# ---- preflights to protected routes -----------------------------------------

KEY = "preflight-test-key-0123456789abcdef"
CALL = {"tool_name": "echo", "parameters": {}}


@pytest.fixture
def guarded(monkeypatch):
    """Protected routes behind the real auth hook and this policy, with a key
    configured. ``ran`` records every view and tool that actually ran."""
    monkeypatch.setenv(auth_guard.API_KEY_ENV, KEY)
    ran = []
    app = Flask(__name__)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(tools_bp)
    app.tool_registry = SimpleNamespace(
        get_tool=lambda name: SimpleNamespace(parameters={}),
        execute_tool=lambda name, **params: ran.append(name) or ToolResult(success=True, output="ran"),
    )

    @app.route("/api/code-execution/handles-options", methods=["POST", "OPTIONS"])
    def handles_options():
        ran.append(request.method)
        return {"ran": True}

    cors_policy.init_cors(app)
    return app.test_client(), ran


def _guarded_preflight(client, path, origin):
    return client.options(path, environ_base=REMOTE, headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-key",
    })


def test_a_preflight_to_a_protected_route_is_answered_for_this_install(guarded):
    client, ran = guarded
    response = _guarded_preflight(client, "/api/tools/execute", "http://localhost:5173")
    assert response.status_code == 200
    assert _allow_origin(response) == "http://localhost:5173"
    assert response.headers.get("Access-Control-Allow-Credentials") == "true"
    assert "x-api-key" in response.headers["Access-Control-Allow-Headers"].lower()
    assert ran == []


@pytest.mark.parametrize("origin", ["https://evil.example", OTHER_DEVICE])
def test_a_preflight_from_another_page_gets_no_cors_headers(guarded, origin):
    client, ran = guarded
    response = _guarded_preflight(client, "/api/tools/execute", origin)
    assert _allow_origin(response) is None
    assert ran == []


def test_the_request_after_the_preflight_still_needs_the_key(guarded):
    client, ran = guarded
    headers = {"Origin": "http://localhost:5173"}
    refused = client.post("/api/tools/execute", json=CALL, headers=headers, environ_base=REMOTE)
    assert refused.status_code == 401
    assert refused.get_json()["code"] == auth_guard.API_KEY_CODE
    assert ran == []
    allowed = client.post("/api/tools/execute", json=CALL, environ_base=REMOTE,
                          headers={**headers, auth_guard.API_KEY_HEADER: KEY})
    assert allowed.status_code == 200
    assert ran == ["echo"]


def test_an_options_a_view_handles_itself_is_still_guarded(guarded):
    client, ran = guarded
    response = _guarded_preflight(client, "/api/code-execution/handles-options", "http://localhost:5173")
    assert response.status_code == 401
    assert ran == []
