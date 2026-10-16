import pytest
from flask import Flask
from importlib import util
from pathlib import Path


_AUTH_GUARD_PATH = Path(__file__).resolve().parents[1] / "utils" / "auth_guard.py"
_AUTH_GUARD_SPEC = util.spec_from_file_location("auth_guard_under_test", _AUTH_GUARD_PATH)
auth_guard = util.module_from_spec(_AUTH_GUARD_SPEC)
assert _AUTH_GUARD_SPEC and _AUTH_GUARD_SPEC.loader
_AUTH_GUARD_SPEC.loader.exec_module(auth_guard)


@pytest.fixture
def app():
    test_app = Flask(__name__)
    test_app.before_request(auth_guard.check_endpoint_auth)

    @test_app.route("/api/files/write", methods=["POST"])
    def files_write():
        return {"ok": True}

    @test_app.route("/api/files/browse", methods=["GET"])
    def files_browse():
        return {"ok": True}

    @test_app.route("/api/self-code/file", methods=["GET"])
    def self_code_file():
        return {"ok": True}

    @test_app.route("/api/memory/clear", methods=["DELETE"])
    def memory_clear():
        return {"ok": True}

    @test_app.route("/api/memory", methods=["GET"])
    def memory_list():
        return {"ok": True}

    @test_app.route("/api/meta/clear-pycache", methods=["POST"])
    def clear_pycache():
        return {"ok": True}

    @test_app.route("/api/meta/rebuild-index", methods=["POST"])
    def meta_mutation():
        return {"ok": True}

    @test_app.route("/api/meta/generation-history", methods=["GET", "DELETE"])
    def generation_history():
        return {"ok": True}

    @test_app.route("/api/audio-foundry/jobs", methods=["GET", "DELETE"])
    def audio_jobs():
        return {"ok": True}

    @test_app.route("/api/settings/profile", methods=["GET", "POST"])
    def settings_profile():
        return {"ok": True}

    @test_app.route("/api/connections", methods=["GET"])
    def connections_list():
        return {"ok": True}

    @test_app.route("/api/connections/publishes/<int:pid>/approve", methods=["POST"])
    def connections_approve(pid):
        return {"ok": True}

    @test_app.route("/api/connections/settings", methods=["GET", "POST"])
    def connections_settings():
        return {"ok": True}

    @test_app.route("/api/social-outreach/status", methods=["GET"])
    def outreach_status():
        return {"ok": True}

    @test_app.route("/api/code-execution/python", methods=["POST"])
    def code_execution_python():
        return {"ok": True}

    @test_app.route("/api/tools/execute", methods=["POST"])
    def tools_execute():
        return {"ok": True}

    @test_app.route("/api/tools/execute_python", methods=["GET"])
    def tools_execute_python_schema():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/execute", methods=["POST"])
    def automation_mcp_execute():
        return {"ok": True, "approved": auth_guard.caller_is_authorized()}

    @test_app.route("/api/code-execution/format", methods=["POST"])
    def code_execution_format():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/servers/<name>", methods=["GET", "PUT", "DELETE"])
    def automation_mcp_server(name):
        return {"ok": True}

    @test_app.route("/api/automation/mcp/connect", methods=["POST"])
    def automation_mcp_connect():
        return {"ok": True}

    @test_app.route("/api/automation/mcp/reload-config", methods=["POST"])
    def automation_mcp_reload():
        return {"ok": True}

    @test_app.route("/api/self-improvement/pending-fixes/<int:fix_id>/apply", methods=["POST"])
    def pending_fix_apply(fix_id):
        return {"ok": True}

    @test_app.route("/api/self-improvement/pending-fixes/<int:fix_id>", methods=["GET"])
    def pending_fix_read(fix_id):
        return {"ok": True}

    @test_app.route("/api/tools/jobs/<job_id>", methods=["GET"])
    def tools_job(job_id):
        return {"ok": True}

    return test_app


CONNECTIONS_REQUESTS = [
    ("GET", "/api/connections"),
    ("POST", "/api/connections/publishes/7/approve"),
    ("POST", "/api/connections/settings"),
]


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_refuse_another_host_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_refuse_a_lan_device_through_the_local_proxy(app, monkeypatch, method, path):
    # The web UI reaches /api through the loopback Vite proxy, which forwards the
    # browser's address in X-Forwarded-For.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_answer_this_machine_without_api_key(app, monkeypatch, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert direct.status_code == 200
    assert proxied.status_code == 200


@pytest.mark.parametrize("method,path", CONNECTIONS_REQUESTS)
def test_connections_need_the_key_once_one_is_configured(app, monkeypatch, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    def send(addr, **headers):
        return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": addr})

    remote_without_key = send("192.168.1.20")
    local_without_key = send("127.0.0.1")
    wrong_key = send("192.168.1.20", **{"X-API-Key": "not-it"})

    assert remote_without_key.status_code == 401
    assert remote_without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    assert local_without_key.status_code == 401
    assert wrong_key.status_code == 401
    assert wrong_key.get_json()["credential_rejected"] is True
    assert send("192.168.1.20", **{"X-API-Key": "secret"}).status_code == 200
    assert send("127.0.0.1", **{"X-API-Key": "secret"}).status_code == 200


@pytest.mark.parametrize("api_key", [None, "secret"])
@pytest.mark.parametrize("addr", ["192.168.1.20", "127.0.0.1"])
def test_connections_get_the_same_answer_as_social_outreach(app, monkeypatch, api_key, addr):
    if api_key:
        monkeypatch.setenv("GUAARDVARK_API_KEY", api_key)
    else:
        monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    outreach = client.get("/api/social-outreach/status", environ_base={"REMOTE_ADDR": addr})
    connections = client.get("/api/connections", environ_base={"REMOTE_ADDR": addr})

    assert connections.status_code == outreach.status_code
    assert connections.get_json() == outreach.get_json()


def test_every_connections_route_refuses_another_host_without_api_key(monkeypatch):
    """Each route the Connections blueprint serves, not only the sampled ones."""
    import re

    from backend.api.connections_api import connections_bp

    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    real_app = Flask(__name__)
    real_app.before_request(auth_guard.check_endpoint_auth)
    real_app.register_blueprint(connections_bp)
    client = real_app.test_client()

    checked = []
    for rule in real_app.url_map.iter_rules():
        if not rule.endpoint.startswith(f"{connections_bp.name}."):
            continue
        path = re.sub(r"<(?:\w+:)?\w+>", "1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
            assert response.status_code == 403, f"{method} {path}"
            checked.append((method, path))

    # Fewer would mean the blueprint lost routes or the walk above missed them.
    assert len(checked) >= 20


def test_profile_write_blocked_from_remote_host_but_readable(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/settings/profile", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.post("/api/settings/profile", json={"name": "creator"}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_delete_generation_history_blocked_from_remote_host(app, monkeypatch):
    # Deleting every generated image, video and audio file is not a "safe"
    # maintenance op like clear-pycache; it stays behind the /api/meta guard.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 403

    # The counts stay readable so the LAN UI can show what would be deleted.
    response = client.get("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert response.status_code == 200

    response = client.delete("/api/meta/generation-history", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200


def test_clear_audio_jobs_blocked_from_remote_host_but_listing_open(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403
    assert client.get("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 200
    assert client.delete("/api/audio-foundry/jobs", environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200


def test_clear_pycache_allowed_from_remote_host(app, monkeypatch):
    # Safe maintenance op: exempt from the host check even though /api/meta is
    # otherwise mutation-protected, so the operator can clear cache from the LAN UI.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/clear-pycache",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_other_meta_mutation_still_blocked_from_remote_host(app, monkeypatch):
    # The carve-out must be narrow: other /api/meta mutations stay protected.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/meta/rebuild-index",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_lan_device_via_local_proxy_is_still_treated_as_remote(app, monkeypatch):
    # Request arrives from the loopback Vite proxy (REMOTE_ADDR=127.0.0.1) but
    # X-Forwarded-For carries the real LAN client → must still be blocked.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_forged_xff_from_direct_remote_peer_is_ignored(app, monkeypatch):
    # A LAN attacker connecting straight to the backend (non-loopback peer) cannot
    # spoof localhost by setting X-Forwarded-For — the header is only trusted from
    # a loopback peer (our own proxy).
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_forged_xff_through_the_proxy_is_ignored(app, monkeypatch):
    # A LAN device sends X-Forwarded-For: 127.0.0.1 to the UI port; the proxy
    # appends the address it really saw, and that last entry is the one trusted.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1, 192.168.1.20"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_genuine_localhost_via_proxy_is_allowed(app, monkeypatch):
    # Operator on the box: browser → loopback proxy → backend, XFF also loopback.
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_remote_file_mutation_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_remote_self_code_read_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/self-code/file",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_read_only_file_browser_get_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/files/browse",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_api_key_required_for_protected_file_endpoint(app, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")

    client = app.test_client()
    missing_key = client.post(
        "/api/files/write",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    with_key = client.post(
        "/api/files/write",
        headers={"X-API-Key": "secret"},
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert missing_key.status_code == 401
    assert with_key.status_code == 200


def test_remote_memory_clear_is_blocked_without_api_key(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.delete(
        "/api/memory/clear",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == auth_guard.LOCAL_ONLY_MESSAGE.format(machine=auth_guard.machine_name())


def test_remote_memory_read_remains_unprotected(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)

    client = app.test_client()
    response = client.get(
        "/api/memory",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


# Network access (Settings → Access) opens the protected routes to the local
# network, key or not, except the routes that run code a caller supplies.

RUNS_CODE_REQUESTS = [
    ("POST", "/api/code-execution/python"),
    ("POST", "/api/tools/execute"),
    ("POST", "/api/automation/mcp/execute"),
    ("PUT", "/api/automation/mcp/servers/evil"),
    ("DELETE", "/api/automation/mcp/servers/evil"),
    ("POST", "/api/automation/mcp/connect"),
    ("POST", "/api/automation/mcp/reload-config"),
    ("POST", "/api/self-improvement/pending-fixes/7/apply"),
]
# Protected routes network access still opens: formatting only runs a
# formatter over the text, and reading a server entry or a staged fix runs
# nothing.
OPENED_BY_NETWORK_ACCESS = [
    ("POST", "/api/files/write"),
    ("POST", "/api/code-execution/format"),
    ("GET", "/api/automation/mcp/servers/evil"),
    ("GET", "/api/self-improvement/pending-fixes/7"),
]
LAN_DEVICE = "192.168.1.20"


@pytest.fixture
def network_access_on(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_NETWORK_ACCESS", "1")


@pytest.mark.parametrize("method,path", OPENED_BY_NETWORK_ACCESS)
def test_network_access_opens_a_protected_route_to_the_local_network(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_this_machine_keeps_passing_under_network_access_once_a_key_is_set(app, monkeypatch, network_access_on, method, path):
    """A browser on the machine itself that never signed in: network access
    passed it before the code routes were set apart, and still does."""
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": "127.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    assert direct.status_code == 200
    assert proxied.status_code == 200


def test_this_machine_needs_the_key_on_code_routes_while_network_access_is_off(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})

    assert response.status_code == 401


def test_the_tool_endpoint_opt_out_does_not_open_code_routes_to_other_hosts(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    monkeypatch.setenv("GUAARDVARK_PROTECT_TOOL_ENDPOINTS", "false")
    client = app.test_client()

    for method, path in (("POST", "/api/tools/execute"), ("POST", "/api/automation/mcp/execute"),
                         ("POST", "/api/automation/mcp/connect")):
        remote = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})
        assert remote.status_code == 403, f"{method} {path}"
        assert remote.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
        assert client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    # The opt-out still opens what it opened before: tool jobs.
    assert client.get("/api/tools/jobs/j1", environ_base={"REMOTE_ADDR": LAN_DEVICE}).status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_network_access_does_not_open_code_execution_to_a_lan_device(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    direct = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})
    proxied = client.open(
        path, method=method, json={},
        headers={"X-Forwarded-For": LAN_DEVICE},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    )

    for response in (direct, proxied):
        assert response.status_code == 403
        assert response.get_json()["code"] == auth_guard.RUNS_CODE_CODE
        assert response.get_json()["error"] == auth_guard.RUNS_CODE_MESSAGE.format(machine=auth_guard.machine_name())


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_code_execution_answers_this_machine_while_network_access_is_on(app, monkeypatch, network_access_on, method, path):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.open(path, method=method, json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})

    assert response.status_code == 200


@pytest.mark.parametrize("method,path", RUNS_CODE_REQUESTS)
def test_code_execution_takes_the_key_from_a_lan_device_while_network_access_is_on(app, monkeypatch, network_access_on, method, path):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    client = app.test_client()

    def send(**headers):
        return client.open(path, method=method, json={}, headers=headers, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    without_key = send()
    wrong_key = send(**{"X-API-Key": "not-it"})

    assert without_key.status_code == 401
    assert without_key.get_json()["code"] == auth_guard.API_KEY_CODE
    assert wrong_key.status_code == 401
    assert wrong_key.get_json()["credential_rejected"] is True
    assert send(**{"X-API-Key": "secret"}).status_code == 200
    # The other protected routes stay open to the device, as before.
    for other_method, other_path in OPENED_BY_NETWORK_ACCESS:
        assert client.open(other_path, method=other_method, json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE}).status_code == 200


def test_code_execution_takes_a_signed_in_browser_on_the_lan(app, monkeypatch, network_access_on):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    monkeypatch.setattr(auth_guard, "request_has_valid_session", lambda: True)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


def test_a_tool_schema_read_is_not_a_tool_call(app, monkeypatch, network_access_on):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.get("/api/tools/execute_python", environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 200


def test_code_execution_outside_the_local_network_is_refused_the_same_way(app, monkeypatch, network_access_on):
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": "8.8.8.8"})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.RUNS_CODE_CODE


def test_caller_is_authorized_agrees_with_the_hook_on_code_routes(app, monkeypatch, network_access_on):
    """automation_api passes caller_is_authorized() as the approval for
    policy-gated MCP tools; it must say what the hook says."""
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    local = client.post("/api/automation/mcp/execute", json={}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert local.status_code == 200
    assert local.get_json()["approved"] is True

    monkeypatch.setenv("GUAARDVARK_API_KEY", "secret")
    with_key = client.post(
        "/api/automation/mcp/execute", json={},
        headers={"X-API-Key": "secret"},
        environ_base={"REMOTE_ADDR": LAN_DEVICE},
    )
    assert with_key.status_code == 200
    assert with_key.get_json()["approved"] is True


def test_network_access_off_keeps_the_local_only_refusal_for_code_routes(app, monkeypatch):
    monkeypatch.delenv("GUAARDVARK_NETWORK_ACCESS", raising=False)
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    client = app.test_client()

    response = client.post("/api/code-execution/python", json={}, environ_base={"REMOTE_ADDR": LAN_DEVICE})

    assert response.status_code == 403
    assert response.get_json()["code"] == auth_guard.LOCAL_ONLY_CODE
