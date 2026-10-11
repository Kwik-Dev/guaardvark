"""GET /api/memory: status=all lists every row; no status leaves out wrong ones;
total counts the matching rows so a caller can page with offset."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask
    from backend.api.memory_api import memory_bp
    from backend.models import AgentMemory, db
except Exception:  # pragma: no cover - environment without Flask/SQLAlchemy
    pytest.skip("Flask or backend modules not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(memory_bp)
    with app.app_context():
        db.create_all()
        rows = [("active", 5), ("archived", 2), ("wrong", 3)]
        n = 0
        for status, count in rows:
            for _ in range(count):
                n += 1
                db.session.add(AgentMemory(id=f"m-{n:02d}", content=f"fact {n}", status=status))
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _statuses(body):
    return sorted(m["status"] for m in body["memories"])


def test_no_status_is_the_working_set_without_wrong_rows(client):
    body = client.get("/api/memory").get_json()
    assert body["success"] and body["total"] == 7
    assert "wrong" not in _statuses(body)


def test_status_all_lists_every_row_wrong_ones_included(client):
    body = client.get("/api/memory?status=all").get_json()
    assert body["total"] == 10
    assert _statuses(body).count("wrong") == 3


def test_one_status_still_filters(client):
    body = client.get("/api/memory?status=wrong").get_json()
    assert body["total"] == 3 and set(_statuses(body)) == {"wrong"}


def test_offset_pages_through_the_same_total(client):
    first = client.get("/api/memory?status=all&limit=4&offset=0").get_json()
    second = client.get("/api/memory?status=all&limit=4&offset=4").get_json()
    third = client.get("/api/memory?status=all&limit=4&offset=8").get_json()
    assert (first["total"], second["total"], third["total"]) == (10, 10, 10)
    ids = [m["id"] for page in (first, second, third) for m in page["memories"]]
    assert len(ids) == 10 and len(set(ids)) == 10
