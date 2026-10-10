"""Rule <-> project links through /api/entity-links (project_rules_association)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

try:
    from flask import Flask

    from backend.api.entity_links_api import entity_links_bp
    from backend.models import Project, Rule, db
except Exception:  # pragma: no cover - environment without Flask/SQLAlchemy
    pytest.skip("Flask or backend models not available", allow_module_level=True)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(entity_links_bp)
    with app.app_context():
        db.create_all()
        rule = Rule(name="tone", level="USER_GLOBAL", type="PROMPT_TEMPLATE", rule_text="Be brief.")
        db.session.add_all([rule, Project(name="Launch"), Project(name="Site")])
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _project_ids(client, rule_id):
    body = client.get(f"/api/entity-links/rule/{rule_id}/links").get_json()
    assert body["success"] is True
    return sorted(p["id"] for p in body["links"]["projects"])


def test_rule_links_start_empty(client):
    assert _project_ids(client, 1) == []


def test_rule_can_be_linked_to_projects(client):
    res = client.put("/api/entity-links/rule/1/links/projects", json={"linked_ids": [1, 2]})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["linked_count"] == 2
    assert _project_ids(client, 1) == [1, 2]
    # The same relation seen from the project side.
    body = client.get("/api/entity-links/project/1/links").get_json()
    assert [r["id"] for r in body["links"]["rules"]] == [1]
    assert body["links"]["rules"][0]["name"] == "tone"


def test_put_replaces_the_set(client):
    client.put("/api/entity-links/rule/1/links/projects", json={"linked_ids": [1, 2]})
    res = client.put("/api/entity-links/rule/1/links/projects", json={"linked_ids": [2]})
    assert res.status_code == 200
    assert _project_ids(client, 1) == [2]
    res = client.put("/api/entity-links/rule/1/links/projects", json={"linked_ids": []})
    assert res.status_code == 200
    assert _project_ids(client, 1) == []


def test_unknown_project_id_is_refused_not_partially_applied(client):
    res = client.put("/api/entity-links/rule/1/links/projects", json={"linked_ids": [1, 99]})
    assert res.status_code == 404
    assert "99" in res.get_json()["error"]
    assert _project_ids(client, 1) == []


def test_project_side_put_uses_the_same_relation(client):
    res = client.put("/api/entity-links/project/2/links/rules", json={"linked_ids": [1]})
    assert res.status_code == 200
    assert _project_ids(client, 1) == [2]


def test_unknown_rule_is_404(client):
    res = client.get("/api/entity-links/rule/42/links")
    assert res.status_code == 404
    assert "not found" in res.get_json()["error"]


def test_rules_are_linkable_by_name(client):
    body = client.get("/api/entity-links/linkable/rule?search=ton").get_json()
    assert body["count"] == 1
    assert body["entities"][0] == {
        "id": 1,
        "name": "tone",
        "level": "USER_GLOBAL",
        "type": "PROMPT_TEMPLATE",
        "is_active": True,
    }
