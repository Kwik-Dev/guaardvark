"""`guaardvark settings list|get|set` against a fake backend.

The list used to be a hand-kept seven-key list with `get` accepting more keys and
`set` posting a body the typed routes did not read. These pin the fix: `list` reads the
backend's canonical endpoint, `get` unwraps the route's single field, and `set` refuses
a studio-only key instead of half-writing it.
"""
import json

from llx.main import app


def _invoke(cli_runner, args):
    return cli_runner.invoke(app, args)


def test_list_reads_the_canonical_endpoint(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/settings", json={
        "success": True,
        "data": {
            "settings": {"web_access": False, "behavior_learning": True},
            "settable": {"web_access": True, "behavior_learning": True},
        },
    })

    result = _invoke(cli_runner, ["settings", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["settings"]["web_access"] is False
    assert payload["data"]["settable"]["behavior_learning"] is True
    # One call to the canonical endpoint, not one call per key.
    assert len(fake_backend.calls_for("GET", "/api/settings")) == 1
    assert fake_backend.calls_for("GET", "/api/settings/web_access") == []


def test_get_unwraps_the_route_field(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/settings/web_access", json={
        "success": True, "data": {"allow_web_search": True},
    })

    result = _invoke(cli_runner, ["settings", "get", "web_access", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # The value, not the nested {"allow_web_search": ...} the route returns.
    assert payload["data"]["web_access"] is True


def test_set_posts_the_canonical_body(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/settings", json={
        "success": True, "data": {"settings": {"web_access": False}, "settable": {"web_access": True}},
    })
    fake_backend.route("POST", "/api/settings/web_access", json={
        "success": True, "data": {"allow_web_search": True},
    })

    result = _invoke(cli_runner, ["settings", "set", "web_access", "true", "--json"])
    assert result.exit_code == 0, result.output
    sent = fake_backend.calls_for("POST", "/api/settings/web_access")
    assert len(sent) == 1
    assert json.loads(sent[0][2]) == {"web_access": True}


def test_set_refuses_a_studio_only_key_without_posting(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/settings", json={
        "success": True, "data": {"settings": {"profile": {}}, "settable": {"profile": False}},
    })

    result = _invoke(cli_runner, ["settings", "set", "profile", "x", "--json"])
    assert result.exit_code == 1
    assert fake_backend.posted_paths() == []
