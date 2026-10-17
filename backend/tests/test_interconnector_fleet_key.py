"""The Interconnector's fleet key: how it is checked and who may mint it.

The master stores only a SHA-256 hash of the key its clients send. The check
compares hashes in constant time. A new key is made only for the person at the
master and shown once; a caller on another machine never receives one, with
or without the install API key.
"""
import hashlib
import json

import pytest
from flask import Flask

from backend.api import interconnector_api as ic_api
from backend.models import Setting, db
from backend.utils import auth_guard

OTHER_MACHINE = {"REMOTE_ADDR": "192.0.2.44"}
THIS_MACHINE = {"REMOTE_ADDR": "127.0.0.1"}
GENERATE = "/api/interconnector/config/generate-key"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _clear_config_cache():
    ic_api._config_cache["config"] = None
    ic_api._config_cache["expires"] = 0


@pytest.fixture
def master(monkeypatch):
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv(auth_guard.API_KEY_ENV, raising=False)
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.before_request(auth_guard.check_endpoint_auth)
    app.register_blueprint(ic_api.interconnector_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Setting(key="interconnector_config", value=json.dumps(
            {"is_enabled": True, "node_mode": "master", "node_name": "m",
             "master_api_key": "kept-secret"})))
        db.session.commit()
        _clear_config_cache()
        yield app
        db.session.remove()
        db.drop_all()
        _clear_config_cache()


def _stored_config(app):
    with app.app_context():
        return json.loads(db.session.get(Setting, "interconnector_config").value)


def test_this_machine_gets_the_key_once_and_only_its_hash_is_stored(master):
    response = master.test_client().post(GENERATE, environ_base=THIS_MACHINE)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    key = response.get_json()["data"]["api_key"]
    assert len(key) >= 40

    stored = _stored_config(master)
    assert stored["api_key_hash"] == _hash(key)
    assert key not in json.dumps(stored)


def test_the_config_reply_carries_the_hash_never_the_key(master):
    client = master.test_client()
    key = client.post(GENERATE, environ_base=THIS_MACHINE).get_json()["data"]["api_key"]
    body = client.get("/api/interconnector/config", environ_base=THIS_MACHINE).get_data(as_text=True)
    assert key not in body
    assert _hash(key) in body
    assert "kept-secret" not in body


@pytest.mark.parametrize("environ,headers", [
    (OTHER_MACHINE, {}),
    # A device on the LAN arriving through the UI's own proxy on this machine.
    (THIS_MACHINE, {"X-Forwarded-For": "192.0.2.44"}),
])
def test_another_machine_without_a_key_is_refused_and_nothing_changes(master, environ, headers):
    response = master.test_client().post(GENERATE, environ_base=environ, headers=headers)
    assert response.status_code == 403
    assert "api_key" not in response.get_data(as_text=True)
    assert "api_key_hash" not in _stored_config(master)


def test_another_machine_with_the_install_key_still_gets_no_fleet_key(master, monkeypatch):
    monkeypatch.setenv(auth_guard.API_KEY_ENV, "install-key")
    response = master.test_client().post(
        GENERATE, environ_base=OTHER_MACHINE, headers={"X-API-Key": "install-key"})
    assert response.status_code == 403
    body = response.get_json()
    assert body["error"]["code"] == auth_guard.LOCAL_ONLY_CODE
    assert "data" not in body
    assert "api_key_hash" not in _stored_config(master)


def test_this_machine_needs_the_install_key_once_one_is_set(master, monkeypatch):
    monkeypatch.setenv(auth_guard.API_KEY_ENV, "install-key")
    client = master.test_client()
    assert client.post(GENERATE, environ_base=THIS_MACHINE).status_code == 401
    response = client.post(GENERATE, environ_base=THIS_MACHINE, headers={"X-API-Key": "install-key"})
    assert response.status_code == 200
    assert _stored_config(master)["api_key_hash"] == _hash(response.get_json()["data"]["api_key"])


def test_a_client_node_makes_no_fleet_key(master):
    with master.app_context():
        setting = db.session.get(Setting, "interconnector_config")
        setting.value = json.dumps({"is_enabled": True, "node_mode": "client"})
        db.session.commit()
        _clear_config_cache()
    assert master.test_client().post(GENERATE, environ_base=THIS_MACHINE).status_code == 400


def test_verify_accepts_the_key_whose_hash_is_stored():
    assert ic_api._verify_api_key("fleet-key", _hash("fleet-key")) is True


@pytest.mark.parametrize("provided", ["fleet-kez", "", None, "fleet-key ", "ключ"])
def test_verify_refuses_any_other_key(provided):
    assert ic_api._verify_api_key(provided, _hash("fleet-key")) is False


@pytest.mark.parametrize("stored", ["", None])
def test_verify_refuses_when_no_hash_is_stored(stored):
    assert ic_api._verify_api_key("fleet-key", stored) is False


def test_verify_compares_in_constant_time(monkeypatch):
    seen = []
    real = ic_api.hmac.compare_digest

    def recording(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ic_api.hmac, "compare_digest", recording)
    assert ic_api._verify_api_key("fleet-key", _hash("fleet-key")) is True
    assert seen == [(_hash("fleet-key").encode(), _hash("fleet-key").encode())]
