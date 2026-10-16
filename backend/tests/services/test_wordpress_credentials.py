"""WordPress site keys: kept in the credential store, never in the database or
an API response, and moved there from older installs without loss."""

import importlib
import json

import pytest

from backend.services import wordpress_credentials as wpc
from backend.utils import credential_store as cs

KEY = "wp-app-pass-1234"
URL = "https://blog.example.test"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("GUAARDVARK_CREDENTIALS_PLAINTEXT", raising=False)
    importlib.reload(cs)
    yield
    importlib.reload(cs)


@pytest.fixture
def db_app():
    """Flask app on an in-memory database with the WordPress routes mounted."""
    from flask import Flask

    from backend.api.wordpress_api import wordpress_bp
    from backend.models import db

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(wordpress_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def remote(monkeypatch):
    """Stand-in for the WordPress site: records the key each connection test sent."""
    from backend.api import wordpress_api

    sent = []

    class FakeService:
        def __init__(self, site_url, api_key, username=None, timeout=30):
            self.api_key = api_key

        def test_connection(self):
            sent.append(self.api_key)
            return True, None

    monkeypatch.setattr(wordpress_api, "LlamanatorAPIService", FakeService)
    monkeypatch.setattr(wordpress_api, "WordPressAPIService", FakeService)
    return sent


def _legacy_row(api_key, url=URL):
    """A row as an older install wrote it, key in the column."""
    from backend.models import WordPressSite, db

    site = WordPressSite(url=url, username="", api_key=api_key, connection_type="llamanator")
    db.session.add(site)
    db.session.commit()
    return site


def _column(site_id):
    from sqlalchemy import text

    from backend.models import db

    return db.session.execute(
        text("SELECT api_key FROM wordpress_sites WHERE id = :i"), {"i": site_id}
    ).scalar()


# --- form input ------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "", "   ", "••••1234", "****", 7])
def test_blank_or_masked_input_is_no_new_key(value):
    assert wpc.incoming_key(value) is None


def test_typed_key_is_trimmed():
    assert wpc.incoming_key("  abcd efgh  ") == "abcd efgh"


# --- the API ---------------------------------------------------------------------
def test_register_stores_the_key_encrypted_and_never_returns_it(db_app, remote):
    client = db_app.test_client()

    response = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY})

    assert response.status_code == 201
    body = response.get_json()["data"]
    assert KEY not in json.dumps(response.get_json())
    assert body["has_api_key"] is True
    assert body["api_key_hint"] == "••••1234"
    assert body["api_key_encrypted"] is True
    assert remote == [KEY]
    assert _column(body["id"]) == ""
    assert KEY not in cs.credentials_path().read_text()
    assert cs.get_secret(wpc.site_ref(body["id"]))["api_key"] == KEY


def test_list_and_get_never_return_the_key(db_app, remote):
    client = db_app.test_client()
    site_id = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY}).get_json()["data"]["id"]

    for path in ("/api/wordpress/sites", f"/api/wordpress/sites/{site_id}"):
        assert KEY not in client.get(path).get_data(as_text=True)


def test_update_with_a_blank_key_keeps_the_stored_one(db_app, remote):
    client = db_app.test_client()
    site_id = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY}).get_json()["data"]["id"]

    response = client.put(f"/api/wordpress/sites/{site_id}", json={"site_name": "Blog", "api_key": ""})

    assert response.status_code == 200
    assert cs.get_secret(wpc.site_ref(site_id))["api_key"] == KEY


def test_update_with_a_new_key_replaces_it_and_tests_with_it(db_app, remote):
    client = db_app.test_client()
    site_id = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY}).get_json()["data"]["id"]

    response = client.put(f"/api/wordpress/sites/{site_id}", json={"api_key": "new-key-9876"})

    assert response.status_code == 200
    assert response.get_json()["data"]["api_key_hint"] == "••••9876"
    assert remote[-1] == "new-key-9876"
    assert cs.get_secret(wpc.site_ref(site_id))["api_key"] == "new-key-9876"
    assert _column(site_id) == ""


def test_test_route_sends_the_stored_key(db_app, remote):
    client = db_app.test_client()
    site_id = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY}).get_json()["data"]["id"]

    response = client.post(f"/api/wordpress/sites/{site_id}/test")

    assert response.status_code == 200
    assert remote[-1] == KEY


def test_test_route_says_so_when_no_key_is_stored(db_app, remote):
    site = _legacy_row("")
    client = db_app.test_client()

    response = client.post(f"/api/wordpress/sites/{site.id}/test")

    assert response.status_code == 400
    assert response.get_json()["error"] == wpc.MISSING_KEY_MESSAGE
    assert remote == []


def test_delete_removes_the_stored_key(db_app, remote):
    client = db_app.test_client()
    site_id = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY}).get_json()["data"]["id"]

    assert client.delete(f"/api/wordpress/sites/{site_id}").status_code == 200
    assert wpc.site_ref(site_id) not in cs.list_refs()


def test_register_stores_nothing_when_the_store_cannot_write(db_app, remote, monkeypatch):
    from backend.models import WordPressSite

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(cs, "set_secret", refuse)
    client = db_app.test_client()

    response = client.post("/api/wordpress/sites", json={"url": URL, "api_key": KEY})

    assert response.status_code == 500
    assert KEY not in response.get_data(as_text=True)
    assert WordPressSite.query.count() == 0


# --- moving keys out of the database -----------------------------------------------
def test_migration_moves_a_column_key_and_empties_the_column(db_app):
    site = _legacy_row(KEY)

    result = wpc.migrate_database_keys()

    assert result == {"moved": 1, "already_stored": 0, "failed": 0}
    assert _column(site.id) == ""
    assert wpc.get_api_key(site) == KEY
    assert KEY not in cs.credentials_path().read_text()


def test_migration_is_idempotent(db_app):
    site = _legacy_row(KEY)

    wpc.migrate_database_keys()
    second = wpc.migrate_database_keys()

    assert second == {"moved": 0, "already_stored": 0, "failed": 0}
    assert wpc.get_api_key(site) == KEY


def test_migration_skips_rows_without_a_key(db_app):
    site = _legacy_row("")

    assert wpc.migrate_database_keys() == {"moved": 0, "already_stored": 0, "failed": 0}
    assert wpc.site_ref(site.id) not in cs.list_refs()


def test_migration_leaves_the_key_when_the_store_write_fails(db_app, monkeypatch):
    site = _legacy_row(KEY)

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cs, "set_secret", refuse)
    result = wpc.migrate_database_keys()

    assert result["failed"] == 1
    assert _column(site.id) == KEY
    assert wpc.get_api_key(site) == KEY


def test_migration_leaves_the_key_when_the_store_does_not_read_it_back(db_app, monkeypatch):
    site = _legacy_row(KEY)
    monkeypatch.setattr(cs, "set_secret", lambda *args, **kwargs: None)

    result = wpc.migrate_database_keys()

    assert result["failed"] == 1
    assert _column(site.id) == KEY


def test_migration_does_not_write_over_an_unreadable_credential_file(db_app):
    site = _legacy_row(KEY)
    cs.config_dir().mkdir(parents=True, exist_ok=True)
    cs.credentials_path().write_text("{not json")

    result = wpc.migrate_database_keys()

    assert result["failed"] == 1
    assert _column(site.id) == KEY
    assert cs.credentials_path().read_text() == "{not json"


def test_a_key_already_in_the_store_wins_over_an_older_column_copy(db_app):
    from backend.models import db

    site = _legacy_row("")
    wpc.set_api_key(site, "current-key-5555")
    db.session.commit()
    site.api_key = "older-key-0000"  # what restoring an older backup brings back
    db.session.commit()

    result = wpc.migrate_database_keys()

    assert result == {"moved": 0, "already_stored": 1, "failed": 0}
    assert _column(site.id) == ""
    assert wpc.get_api_key(site) == "current-key-5555"


def test_a_record_saved_for_another_url_is_never_used(db_app):
    site = _legacy_row("")
    cs.set_secret(wpc.site_ref(site.id), {"api_key": "other-site-key", "site_url": "https://other.example.test"})

    assert wpc.get_api_key(site) == ""
    assert wpc.key_status(site)["configured"] is False

    site.api_key = KEY
    from backend.models import db

    db.session.commit()
    assert wpc.migrate_database_keys()["moved"] == 1
    assert wpc.get_api_key(site) == KEY
