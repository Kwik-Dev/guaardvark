"""GET /api/rules serves one page and the real count in X-Total-Count."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask

    from backend.api.rules_api import rules_bp
    from backend.models import Rule, db
except Exception:  # pragma: no cover - environment without Flask/SQLAlchemy
    pytest.skip("Flask or backend models not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(rules_bp)
    with app.app_context():
        db.create_all()
        for i in range(53):
            db.session.add(
                Rule(
                    name=f"rule_{i:02d}",
                    level="LEARNED" if i < 3 else "USER_GLOBAL",
                    type="PROMPT_TEMPLATE",
                    rule_text="x",
                )
            )
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_default_page_is_50_rows_but_total_is_real(client):
    res = client.get("/api/rules")
    assert res.status_code == 200
    assert len(res.get_json()) == 50
    assert res.headers["X-Total-Count"] == "53"
    assert res.headers["X-Page"] == "1"
    assert res.headers["X-Per-Page"] == "50"


def test_second_page_holds_the_rest(client):
    res = client.get("/api/rules?page=2&per_page=50")
    assert len(res.get_json()) == 3
    assert res.headers["X-Total-Count"] == "53"
    assert res.headers["X-Page"] == "2"


def test_per_page_is_capped_at_100(client):
    res = client.get("/api/rules?per_page=500")
    assert len(res.get_json()) == 53
    assert res.headers["X-Per-Page"] == "100"


def test_total_follows_the_filter(client):
    res = client.get("/api/rules?level=LEARNED")
    assert len(res.get_json()) == 3
    assert res.headers["X-Total-Count"] == "3"


def test_body_stays_a_plain_array_for_existing_callers(client):
    body = client.get("/api/rules?per_page=2").get_json()
    assert isinstance(body, list)
    assert set(body[0]) >= {"id", "name", "level", "type", "is_active"}
