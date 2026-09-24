"""End-to-end: `llx mcp client` against a real in-process backend.

A Flask server runs the actual MCP client service and automation API with a
stdio MCP fixture server, and the real llx CLI talks to it over HTTP.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLI_DIR = os.path.join(REPO, "cli")
FIXTURE = os.path.join(REPO, "backend", "tests", "fixtures", "mcp_echo_server.py")
sys.path.insert(0, REPO)

pytest.importorskip("flask")
pytest.importorskip("mcp")
pytest.importorskip("typer")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mcp_e2e")
    cfg = tmp / "mcp_servers.json"
    cfg.write_text(json.dumps({"mcpServers": {"fx": {"command": sys.executable, "args": [FIXTURE],
                                                     "timeout": 10}}}))
    os.environ["GUAARDVARK_MODE"] = "test"
    os.environ.pop("GUAARDVARK_API_KEY", None)

    from flask import Flask

    from backend import config as app_config
    from backend.services import mcp_client_service as mcs

    app_config.MCP_CONFIG_FILE = str(cfg)
    app_config.MCP_SERVERS_CONFIG = ""
    app_config.LOG_DIR = str(tmp / "logs")
    mcs.MCP_ENABLED = True
    mcs.MCPClientService._instance = None

    from backend.api.automation_api import automation_bp
    from backend.utils.auth_guard import check_endpoint_auth
    from werkzeug.serving import make_server

    app = Flask(__name__)
    app.register_blueprint(automation_bp)
    app.before_request(check_endpoint_auth)

    assert mcs.get_mcp_service().connect("fx")["success"]

    port = _free_port()
    http = make_server("127.0.0.1", port, app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)

    yield f"http://127.0.0.1:{port}"

    http.shutdown()
    mcs.get_mcp_service().shutdown_sync()


def _llx(server, *args, timeout=90):
    env = {**os.environ, "PYTHONPATH": CLI_DIR, "HOME": os.environ.get("HOME", "/tmp")}
    env.pop("GUAARDVARK_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "llx.main", "--server", server, *args],
        cwd=CLI_DIR, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )


def test_mcp_commands(backend):
    res = _llx(backend, "mcp", "client", "servers", "--json")
    assert res.returncode == 0, res.stderr
    servers = json.loads(res.stdout)["servers"]
    assert servers[0]["name"] == "fx" and servers[0]["status"] == "connected"

    res = _llx(backend, "mcp", "client", "tools", "fx", "--json")
    policies = {t["name"]: t["policy"] for t in json.loads(res.stdout)["tools"]}
    assert policies["add"] == "allow" and policies["delete_thing"] == "confirm"

    res = _llx(backend, "mcp", "client", "call", "fx", "add", "--arg", "a=2", "--arg", "b=40", "--json")
    assert res.returncode == 0 and json.loads(res.stdout)["text"] == "42"


def test_mcp_call_destructive_requires_approval(backend):
    denied = _llx(backend, "mcp", "client", "call", "fx", "delete_thing", "--arg", "name=x", "--json")
    assert denied.returncode == 2 and "Not run" in denied.stderr
    ok = _llx(backend, "mcp", "client", "call", "fx", "delete_thing", "--arg", "name=x",
              "--approve", "delete_*", "--json")
    assert ok.returncode == 0 and json.loads(ok.stdout)["text"] == "deleted x"
