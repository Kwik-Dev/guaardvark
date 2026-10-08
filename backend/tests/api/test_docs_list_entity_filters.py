"""GET /api/docs filters by the client or website a document is linked to.

The Files page uses these filters to list one client's or one website's
documents, which the Clients and Websites "Files" menu items open.
"""

import pytest
from flask import Flask

from backend.models import Client, Document, Website, db


@pytest.fixture
def client_app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    from backend.api.docs_api import docs_bp

    if docs_bp.name not in app.blueprints:
        app.register_blueprint(docs_bp)
    with app.app_context():
        db.create_all()
        acme = Client(name="Acme")
        other = Client(name="Other")
        db.session.add_all([acme, other])
        db.session.flush()
        site = Website(url="https://acme.example", client_id=acme.id)
        db.session.add(site)
        db.session.flush()
        db.session.add_all([
            Document(filename="brief.pdf", path="brief.pdf", client_id=acme.id),
            Document(filename="sitemap.txt", path="sitemap.txt", client_id=acme.id, website_id=site.id),
            Document(filename="unrelated.txt", path="unrelated.txt", client_id=other.id),
            Document(filename="loose.txt", path="loose.txt"),
        ])
        db.session.commit()
        yield app, acme.id, site.id
        db.session.remove()
        db.drop_all()


def _names(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return sorted(d["filename"] for d in resp.get_json()["documents"])


def test_client_filter_lists_only_that_clients_documents(client_app):
    app, acme_id, _ = client_app
    resp = app.test_client().get(f"/api/docs/?client_id={acme_id}&per_page=100")
    assert _names(resp) == ["brief.pdf", "sitemap.txt"]


def test_website_filter_lists_only_that_websites_documents(client_app):
    app, _, site_id = client_app
    resp = app.test_client().get(f"/api/docs/?website_id={site_id}&per_page=100")
    assert _names(resp) == ["sitemap.txt"]


def test_no_filter_still_lists_everything(client_app):
    app, _, _ = client_app
    resp = app.test_client().get("/api/docs/?per_page=100")
    assert _names(resp) == ["brief.pdf", "loose.txt", "sitemap.txt", "unrelated.txt"]
