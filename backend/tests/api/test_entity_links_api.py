"""GET /api/entity-links/linkable/<type>?search=  (the link pickers' search box).

The term is matched literally: '%', '_' and '\\' in it match themselves, not
LIKE wildcards, on every entity type the route searches.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

from backend.api.entity_links_api import entity_links_bp  # noqa: E402
from backend.models import Client, Document, Task, db  # noqa: E402


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(entity_links_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _add(app, *rows):
    with app.app_context():
        db.session.add_all(rows)
        db.session.commit()


def _names(client, entity_type, term, key):
    resp = client.get(f"/api/entity-links/linkable/{entity_type}", query_string={"search": term})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["success"] is True
    return sorted(e[key] for e in body["entities"])


class TestLinkableSearchIsLiteral:
    def test_percent_matches_only_a_literal_percent(self, app, client):
        _add(app,
             Document(filename="report 50%.pdf", path="/u/a.pdf"),
             Document(filename="report 500.pdf", path="/u/b.pdf"))

        assert _names(client, "document", "50%", "filename") == ["report 50%.pdf"]

    def test_underscore_matches_only_a_literal_underscore(self, app, client):
        _add(app, Task(name="my_task"), Task(name="myxtask"))

        assert _names(client, "task", "my_task", "name") == ["my_task"]

    def test_backslash_matches_only_a_literal_backslash(self, app, client):
        _add(app, Client(name="C:\\tools"), Client(name="C:tools"))

        assert _names(client, "client", "C:\\tools", "name") == ["C:\\tools"]

    def test_plain_term_still_matches_anywhere_ignoring_case(self, app, client):
        _add(app,
             Document(filename="Quarterly Review.PDF", path="/u/q.pdf"),
             Document(filename="notes.txt", path="/u/n.txt"))

        assert _names(client, "document", "quarterly", "filename") == ["Quarterly Review.PDF"]
