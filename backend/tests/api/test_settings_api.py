"""`/api/settings` — the list/get/set the `guaardvark settings` CLI relies on.

The CLI used to hardcode seven keys and post `{key: value}` at typed routes that read
a differently named field, so `settings set web_access true` wrote False. These pin the
backend half: one canonical list, the canonical-key alias on the typed setters, and the
generic get/set for a registry key whose name is not a route path.
"""
import pytest

try:
    from flask import Flask
    from backend.models import db, Setting
    from backend.api.settings_api import settings_bp
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(settings_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


# --- the canonical list ------------------------------------------------------


def test_list_has_the_cli_keys_and_drops_the_nonexistent_one(client):
    data = client.get("/api/settings").get_json()["data"]
    assert {"web_access", "behavior_learning", "advanced_debug", "chat_image_model"} <= set(
        data["settings"]
    )
    # Listed by the old CLI, but its route is `rag-features`; the canonical key is
    # `rag_features` and `rag_debug` was never real.
    assert "rag_debug" not in data["settings"]
    assert "rag_features" in data["settings"]


def test_list_marks_composite_keys_not_settable(client):
    data = client.get("/api/settings").get_json()["data"]
    assert data["settable"]["web_access"] is True
    assert data["settable"]["chat_image_model"] is True
    assert data["settable"]["profile"] is False
    assert data["settable"]["active_video_model"] is False


# --- the silent-wrong-write fix ---------------------------------------------


def test_post_web_access_accepts_the_canonical_key(client, app):
    resp = client.post("/api/settings/web_access", json={"web_access": True})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["allow_web_search"] is True
    with app.app_context():
        assert db.session.get(Setting, "allow_web_search").value == "true"


def test_post_web_access_still_accepts_the_studio_field(client, app):
    resp = client.post("/api/settings/web_access", json={"allow_web_search": False})
    assert resp.status_code == 200
    assert resp.get_json()["data"]["allow_web_search"] is False
    with app.app_context():
        assert db.session.get(Setting, "allow_web_search").value == "false"


def test_post_behavior_learning_accepts_the_canonical_key(client, app):
    client.post("/api/settings/behavior_learning", json={"behavior_learning": True})
    with app.app_context():
        assert db.session.get(Setting, "behavior_learning_enabled").value == "true"


def test_post_chat_image_model_accepts_the_canonical_key(client, app):
    resp = client.post("/api/settings/chat_image_model", json={"chat_image_model": "zimage-turbo"})
    assert resp.get_json()["data"]["model"] == "zimage-turbo"


# --- the generic fallback routes --------------------------------------------


def test_generic_get_serves_a_key_whose_path_differs(client):
    """`rag_features` has no `/rag_features` route (the path is `/rag-features`)."""
    resp = client.get("/api/settings/rag_features")
    assert resp.status_code == 200
    assert "rag_features" in resp.get_json()["data"]


def test_unknown_key_is_404(client):
    assert client.get("/api/settings/definitely_not_a_setting").status_code == 404
    assert (
        client.post("/api/settings/definitely_not_a_setting", json={}).status_code == 404
    )


def test_generic_routes_serve_an_unrouted_registry_key(client, app, monkeypatch):
    from backend.api import settings_api as sa

    monkeypatch.setitem(sa.SETTINGS_REGISTRY, "synthetic_flag", {
        "get": lambda: "hello",
        "set": lambda v: sa._write_bool_setting("synthetic_flag", sa._as_bool(v)),
        "type": "bool",
        "description": "synthetic",
    })
    assert client.get("/api/settings/synthetic_flag").get_json()["data"] == {
        "synthetic_flag": "hello"
    }
    resp = client.post("/api/settings/synthetic_flag", json={"synthetic_flag": True})
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.get(Setting, "synthetic_flag").value == "true"


def test_generic_post_refuses_a_composite_key(client, monkeypatch):
    from backend.api import settings_api as sa

    monkeypatch.setitem(sa.SETTINGS_REGISTRY, "synthetic_composite", {
        "get": lambda: {},
        "set": None,
        "type": "json",
        "description": "synthetic",
    })
    resp = client.post("/api/settings/synthetic_composite", json={"synthetic_composite": "x"})
    assert resp.status_code == 400


def test_registry_covers_every_settings_get_route():
    """A new GET setting route must be added to SETTINGS_REGISTRY, or `list` misses it."""
    from backend.api import settings_api as sa

    app = Flask(__name__)
    app.register_blueprint(sa.settings_bp)
    excluded = {
        "/api/settings",
        "/api/settings/",
        "/api/settings/<key>",
        "/api/settings/password/requirements",
        "/api/settings/security/check",
    }
    missing = []
    for rule in app.url_map.iter_rules():
        rule_s = str(rule)
        if not rule_s.startswith("/api/settings") or "GET" not in (rule.methods or set()):
            continue
        if rule_s in excluded:
            continue
        key = rule_s[len("/api/settings/"):].replace("-", "_") if rule_s.startswith(
            "/api/settings/"
        ) else ""
        if key not in sa.SETTINGS_REGISTRY:
            missing.append((rule_s, key))
    assert not missing, f"settings GET routes with no SETTINGS_REGISTRY entry: {missing}"


def test_every_registry_get_returns_a_value(client):
    """A raising `get` is swallowed into a null; the list must not hide that."""
    settings = client.get("/api/settings").get_json()["data"]["settings"]
    nulls = sorted(key for key, value in settings.items() if value is None)
    assert not nulls, f"registry keys whose get returned None: {nulls}"
