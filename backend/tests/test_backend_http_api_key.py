"""The MCP server's calls to its own backend carry the key that backend runs with.

For this checkout's backend the key in its .env wins, read on every call, so a
key created or replaced in Settings works without restarting the MCP client;
a backend named by GUAARDVARK_URL gets GUAARDVARK_API_KEY from the environment.
No backend or network: requests.request is replaced.
"""

from types import SimpleNamespace

import pytest

from backend.utils import backend_http as bh


@pytest.fixture
def checkout_env(monkeypatch):
    values = {}
    monkeypatch.setattr(bh, "_checkout_env_value", lambda key, env_file=None: values.get(key, ""))
    for name in ("GUAARDVARK_URL", "GUAARDVARK_API_KEY", "FLASK_PORT"):
        monkeypatch.delenv(name, raising=False)
    return values


def test_this_checkouts_backend_uses_the_key_in_its_env(checkout_env, monkeypatch):
    checkout_env["GUAARDVARK_API_KEY"] = "current"
    monkeypatch.setenv("GUAARDVARK_API_KEY", "stale-from-a-client-config")
    assert bh.backend_api_key() == "current"


def test_without_one_in_env_the_environment_key_is_used(checkout_env, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", " from-env ")
    assert bh.backend_api_key() == "from-env"


def test_a_backend_named_by_url_gets_only_the_environment_key(checkout_env, monkeypatch):
    checkout_env["GUAARDVARK_API_KEY"] = "this-machine"
    monkeypatch.setenv("GUAARDVARK_URL", "http://192.0.2.20:5000")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "that-machine")
    assert bh.backend_api_key() == "that-machine"
    monkeypatch.delenv("GUAARDVARK_API_KEY")
    assert bh.backend_api_key() == ""


def test_the_key_goes_out_in_the_header(checkout_env, monkeypatch):
    checkout_env["GUAARDVARK_API_KEY"] = "current"
    checkout_env["FLASK_PORT"] = "5055"
    sent = {}

    def fake_request(method, url, **kwargs):
        sent.update(method=method, url=url, headers=kwargs["headers"])
        return SimpleNamespace(status_code=200, json=lambda: {"data": 1}, content=b"{}", text="{}")

    monkeypatch.setattr("requests.request", fake_request)
    assert bh.request_json("GET", "/api/x").data == 1
    assert sent["url"] == "http://127.0.0.1:5055/api/x"
    assert sent["headers"]["X-API-Key"] == "current"


def test_env_values_last_line_wins(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GUAARDVARK_API_KEY=old\nOTHER=1\nGUAARDVARK_API_KEY='new'\n")
    assert bh._checkout_env_value("GUAARDVARK_API_KEY", env) == "new"
    assert bh._checkout_env_value("MISSING", env) == ""
    assert bh._checkout_env_value("GUAARDVARK_API_KEY", tmp_path / "absent") == ""
