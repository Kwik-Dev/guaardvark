"""scripts/fleet_sim.py against the real master blueprint.

The simulator's requests go to a Flask test client of the Interconnector
blueprint behind the auth guard, on an in-memory database. They arrive from
loopback, as on the master box, so X-Forwarded-For makes each simulated
machine another host. --nodes 1 runs every S1 security check; a few machines
run the whole life (enrol, approve, certificate, heartbeats, protected routes,
removal). A master with a hole in it makes the simulator fail.
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import InterconnectorNode, InterconnectorNodeIdentity, Setting, db
from backend.utils import auth_guard, node_identity

REPO = Path(__file__).resolve().parents[2]
MASTER_URL = "http://master.test:5000"


def _load_sim():
    spec = importlib.util.spec_from_file_location("fleet_sim_under_test", REPO / "scripts" / "fleet_sim.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sim = _load_sim()


class _Reply:
    def __init__(self, response):
        self.status_code = response.status_code
        self.text = response.get_data(as_text=True)

    def json(self):
        return json.loads(self.text)


class _Session:
    """requests.Session stand-in: prepares the request as requests would
    (signing included) and hands it to Flask from loopback."""

    def __init__(self, client):
        self.client = client

    def request(self, method, url, data=None, params=None, headers=None, auth=None, timeout=None):
        prepared = requests.Request(method, url, data=data, params=params, headers=headers,
                                    auth=auth).prepare()
        parts = urlsplit(prepared.url)
        sent = {k: v for k, v in prepared.headers.items() if k.lower() != "content-length"}
        response = self.client.open(parts.path, method=method, query_string=parts.query,
                                    data=prepared.body, headers=sent,
                                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
        return _Reply(response)


@pytest.fixture
def master(tmp_path, monkeypatch):
    import backend.tools.agent_tools.code_manipulation_tools as cmt

    monkeypatch.setattr(node_identity, "DEFAULT_NODE_ID_PATH", tmp_path / "master" / "node_id")
    monkeypatch.setenv(node_identity.NODE_ID_ENV, "master-node")
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    monkeypatch.delenv("CLUSTER_ROLE", raising=False)
    monkeypatch.setattr(ic_api, "get_file_sync_service",
                        lambda: SimpleNamespace(get_project_root=lambda: tmp_path))
    monkeypatch.setattr(ic_api, "_master_core_registry",
                        lambda _root: SimpleNamespace(summary=lambda: {"tracked_files": 0}))
    directives = []
    monkeypatch.setattr(cmt, "_handle_uncle_directive", lambda d, r: directives.append(d))

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master", "node_name": "m",
             "require_api_key": True, "api_key_hash": ic_api._hash_api_key("fleet-key"),
             "sync_entities": ["rules"]})))
        db.session.commit()
        ic_api._config_cache.update(config=None, expires=0)

        client = app.test_client()
        monkeypatch.setattr(sim.Master, "_session", lambda self: _Session(client))
        monkeypatch.setattr(sim.requests, "get",
                            lambda url, timeout=None: _Session(client).request("GET", url))
        app.directives = directives
        yield app
        db.session.remove()
        db.drop_all()
        ic_api._config_cache.update(config=None, expires=0)


def _run(tmp_path, *extra):
    return sim.main(["--master", MASTER_URL, "--fleet-key", "fleet-key",
                     "--data-dir", str(tmp_path / "sim"), "--concurrency", "1", *extra])


def _left_on_master(app):
    with app.app_context():
        return (InterconnectorNodeIdentity.query.count(),
                InterconnectorNode.query.filter(InterconnectorNode.node_name.like("fleet-sim%")).count())


def test_one_machine_passes_every_security_check(master, tmp_path, capsys):
    assert _run(tmp_path, "--nodes", "1", "--approve") == 0
    out = capsys.readouterr().out
    assert "RESULT: clean" in out and "FAIL" not in out
    passed = [line for line in out.splitlines() if line.strip().startswith("PASS")]
    tasks = {line.split()[1] for line in passed}
    assert tasks == {"T438", "T439", "T441", "T442"}
    assert len(passed) >= 20
    # No directive reached the kill switches, and nothing is left behind.
    assert master.directives == []
    assert _left_on_master(master) == (0, 0)


def test_a_small_fleet_runs_clean(master, tmp_path, capsys):
    assert _run(tmp_path, "--nodes", "5", "--approve", "--beats", "2") == 0
    out = capsys.readouterr().out
    assert "Machines with a certificate: 5/5" in out
    assert "RESULT: clean" in out
    assert _left_on_master(master) == (0, 0)


def test_without_approval_the_machines_wait(master, tmp_path, capsys):
    assert _run(tmp_path, "--nodes", "2", "--keep") == 0
    out = capsys.readouterr().out
    assert "wait for approval" in out
    with master.app_context():
        assert {row.status for row in InterconnectorNodeIdentity.query.all()} == {"pending"}


def test_a_wrong_fleet_key_is_reported(master, tmp_path, capsys):
    code = sim.main(["--master", MASTER_URL, "--fleet-key", "wrong", "--data-dir", str(tmp_path / "sim"),
                     "--nodes", "2", "--approve", "--concurrency", "1"])
    assert code == 1
    assert "HTTP 401" in capsys.readouterr().out


def test_a_master_that_lets_one_machine_beat_for_another_fails_the_run(master, tmp_path, capsys, monkeypatch):
    real = ic_api._require_node
    monkeypatch.setattr(ic_api, "_require_node", lambda url_node_id=None: real())
    assert _run(tmp_path, "--nodes", "1", "--approve") == 1
    out = capsys.readouterr().out
    assert "FAIL  T441  B signs a heartbeat for A's node id" in out
