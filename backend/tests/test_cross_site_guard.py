"""A state-changing request that another site's page made the browser send is
refused before any view runs (backend/utils/cross_site_guard.py), and
restarting Guaardvark answers only this machine or the API key
(backend/utils/auth_guard.py).

Drives the hooks in backend/app.py's order (cross-site guard, then auth
guard) and init_cors on the real restart, tools, state and Interconnector
blueprints. Every view that would act is replaced by a recorder and the
restart module cannot start a process; /api/tools/execute runs with a
stand-in tool registry that records the call. No backend, GPU or network.
"""

from types import SimpleNamespace

import pytest
from flask import Flask

from backend.api import reboot_api
from backend.api.interconnector_api import interconnector_bp
from backend.api.state_api import state_bp
from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolResult
from backend.utils import auth_guard, cors_policy
from backend.utils.cross_site_guard import CROSS_SITE_CODE, refuse_cross_site_request

MACHINE = frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.20", "workstation"})
KEY = "cross-site-test-key-0123456789abcdef"
LOCAL = {"REMOTE_ADDR": "127.0.0.1"}
# A device on the LAN, arriving through the UI's own proxy on this machine.
OTHER_DEVICE = {"REMOTE_ADDR": "127.0.0.1"}
OTHER_DEVICE_HEADERS = {"X-Forwarded-For": "192.0.2.77", "Origin": "http://192.168.1.20:5173", "Sec-Fetch-Site": "same-origin"}
EVIL = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}
OWN = {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"}
LAN_NODE = {"Origin": "http://192.168.1.60:5173", "Sec-Fetch-Site": "cross-site"}
FORM = {"data": "a=1", "content_type": "application/x-www-form-urlencoded"}
CALL = {"tool_name": "echo", "parameters": {}}
RECORDED = {
    "/api/reboot": "reboot",
    "/api/reboot/stream": "reboot/stream",
    "/api/reboot/log": "reboot/log",
    "/api/state/code-editor/session": "state/session",
    "/api/interconnector/nodes/register": "node/register",
    "/api/interconnector/nodes/<node_id>/heartbeat": "node/heartbeat",
    "/api/interconnector/config": "interconnector/config",
}


def _no_process(*_args, **_kwargs):
    raise AssertionError("a test tried to start a process")


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setattr(cors_policy, "_machine_hosts", lambda: MACHINE)
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    for name in ("VITE_PORT", "FLASK_PORT", "PORT", "VITE_FRONTEND_URL", "VITE_ALLOWED_HOSTS",
                 cors_policy.EXTRA_ORIGINS_ENV, auth_guard.API_KEY_ENV, auth_guard.TOOL_ENDPOINTS_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reboot_api, "subprocess", SimpleNamespace(Popen=_no_process, PIPE=None, DEVNULL=None))

    ran = []
    app = Flask(__name__)
    app.url_map.strict_slashes = False
    app.before_request(refuse_cross_site_request)
    app.before_request(auth_guard.check_endpoint_auth)
    for blueprint in (reboot_api.reboot_bp, tools_bp, state_bp, interconnector_bp):
        app.register_blueprint(blueprint)
    app.tool_registry = SimpleNamespace(
        get_tool=lambda name: SimpleNamespace(parameters={}),
        execute_tool=lambda name, **params: ran.append("tools/execute") or ToolResult(success=True, output="ran"),
    )

    def recorder(label):
        def view(*_args, **_kwargs):
            ran.append(label)
            return {"ran": label}
        return view

    replaced = set()
    for rule in app.url_map.iter_rules():
        if rule.rule in RECORDED:
            app.view_functions[rule.endpoint] = recorder(RECORDED[rule.rule])
            replaced.add(rule.rule)
    assert replaced == set(RECORDED)
    cors_policy.init_cors(app)
    return app.test_client(), ran


def _send(client, method, path, headers=None, environ=None, **kwargs):
    return client.open(path, method=method, headers=headers or {}, environ_base=environ or LOCAL, **kwargs)


def _code(response):
    return (response.get_json(silent=True) or {}).get("code")


# ---- another site's page ----------------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/reboot", "/api/reboot/stream", "/api/tools/execute", "/api/state/code-editor/session",
])
def test_a_form_post_from_another_site_is_refused_before_any_view(app_client, path):
    client, ran = app_client
    response = _send(client, "POST", path, EVIL, **FORM)
    assert response.status_code == 403
    assert _code(response) == CROSS_SITE_CODE
    assert ran == []


def test_reading_is_not_affected(app_client):
    client, ran = app_client
    assert _send(client, "GET", "/api/state/code-editor/session", EVIL).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Origin": "http://192.168.1.77:8080", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://localhost:3000"},
    {"Origin": "not an origin"},
])
def test_other_signs_of_another_page_are_refused(app_client, headers):
    client, ran = app_client
    response = _send(client, "POST", "/api/state/code-editor/session", headers, **FORM)
    assert _code(response) == CROSS_SITE_CODE
    assert ran == []


# ---- this install and clients that are not browsers --------------------------

@pytest.mark.parametrize("headers", [
    OWN,                                                                   # the UI through its proxy
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-site"},     # a build with an absolute API URL
    {"Origin": "http://192.168.1.20:5173"},                                # a browser without Sec-Fetch-*
    {},                                                                    # CLI, MCP server, curl
    # The UI under a name the list does not know, through the dev/preview proxy or nginx.
    {"Origin": "http://box.lan:5173", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://box.lan:5173", "X-Forwarded-Host": "box.lan:5173", "X-Forwarded-Proto": "http"},
])
def test_this_installs_pages_and_other_clients_pass(app_client, headers):
    client, ran = app_client
    response = _send(client, "POST", "/api/state/code-editor/session", headers, **FORM)
    assert response.status_code == 200
    assert ran == ["state/session"]


@pytest.mark.parametrize("content_type,mode", [("application/json", "cors"), ("text/plain", "no-cors")])
def test_a_same_origin_beacon_passes(app_client, content_type, mode):
    client, ran = app_client
    headers = {**OWN, "Sec-Fetch-Mode": mode}
    response = _send(client, "POST", "/api/state/code-editor/session", headers, data="{}", content_type=content_type)
    assert response.status_code == 200 and ran == ["state/session"]


def test_the_ui_and_the_cli_still_run_tools_on_this_machine(app_client):
    client, ran = app_client
    assert _send(client, "POST", "/api/tools/execute", OWN, json=CALL).status_code == 200
    assert _send(client, "POST", "/api/tools/execute", json=CALL).status_code == 200
    assert ran == ["tools/execute", "tools/execute"]


# ---- the Interconnector -----------------------------------------------------

@pytest.mark.parametrize("path,label", [
    ("/api/interconnector/nodes/register", "node/register"),
    ("/api/interconnector/nodes/n1/heartbeat", "node/heartbeat"),
])
def test_a_client_nodes_ui_still_registers_with_its_master(app_client, path, label):
    client, ran = app_client
    response = _send(client, "POST", path, LAN_NODE, {"REMOTE_ADDR": "192.168.1.60"}, json={})
    assert response.status_code == 200 and ran == [label]


def test_node_routes_still_refuse_other_sites(app_client):
    client, ran = app_client
    assert _code(_send(client, "POST", "/api/interconnector/nodes/register", EVIL, json={})) == CROSS_SITE_CODE
    other = _send(client, "POST", "/api/interconnector/config", LAN_NODE, {"REMOTE_ADDR": "192.168.1.60"}, json={})
    assert _code(other) == CROSS_SITE_CODE
    assert ran == []


def test_calls_between_machines_carry_no_origin_and_pass(app_client):
    client, ran = app_client
    response = _send(client, "POST", "/api/interconnector/nodes/register", None, {"REMOTE_ADDR": "192.168.1.60"}, json={})
    assert response.status_code == 200 and ran == ["node/register"]


def test_the_cluster_proxy_does_not_pass_the_browsers_origin_on():
    from backend.services.cluster_proxy import HttpProxyForwarder

    incoming = {"Origin": "http://192.168.1.20:5173", "Sec-Fetch-Site": "same-site",
                "Sec-Fetch-Mode": "cors", "Content-Type": "application/json"}
    out = HttpProxyForwarder()._sanitize_headers(incoming, SimpleNamespace(api_key="node-key"), None)
    assert "Origin" not in out and not any(k.lower().startswith("sec-fetch-") for k in out)
    assert out["Content-Type"] == "application/json"


# ---- restarting -------------------------------------------------------------

@pytest.mark.parametrize("method,path", [("POST", "/api/reboot"), ("POST", "/api/reboot/stream"), ("GET", "/api/reboot/log")])
def test_restart_answers_only_this_machine_without_a_key(app_client, method, path):
    client, ran = app_client
    refused = _send(client, method, path, OTHER_DEVICE_HEADERS, OTHER_DEVICE)
    assert refused.status_code == 403 and _code(refused) == auth_guard.LOCAL_ONLY_CODE
    assert ran == []
    here = _send(client, method, path, {**OWN, "X-Forwarded-For": "127.0.0.1"})
    assert here.status_code == 200 and len(ran) == 1


def test_restart_needs_the_key_once_one_exists(app_client, monkeypatch):
    client, ran = app_client
    monkeypatch.setenv(auth_guard.API_KEY_ENV, KEY)
    refused = _send(client, "POST", "/api/reboot/stream", OTHER_DEVICE_HEADERS, OTHER_DEVICE)
    assert refused.status_code == 401 and _code(refused) == auth_guard.API_KEY_CODE
    allowed = _send(client, "POST", "/api/reboot/stream", {**OTHER_DEVICE_HEADERS, "X-API-Key": KEY}, OTHER_DEVICE)
    assert allowed.status_code == 200 and ran == ["reboot/stream"]


def test_settings_lists_restarting_as_protected():
    assert "Restarting Guaardvark" in auth_guard.protected_summary()
