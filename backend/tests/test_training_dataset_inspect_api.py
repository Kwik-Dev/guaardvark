"""Add Dataset: the inspect route describes a path the way the trainer will
read it, the locations route names where the picker opens, both are closed to
other hosts like the server folder browser, and saving a dataset refuses a
path the trainer cannot use, with the reason and never the file's text.

Flask test client over an in-memory SQLite database; files on tmp_path."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.models import TrainingDataset, db

ROW = {"instruction": "Name a colour.", "output": "Blue."}


@pytest.fixture
def client():
    from backend.api.training.routes import training_bp
    from backend.api.training_datasets_api import training_bp as datasets_bp

    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    app.register_blueprint(training_bp)
    app.register_blueprint(datasets_bp)
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def _jsonl(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def test_inspect_reports_rows_formats_samples_and_problems(client, tmp_path):
    data = _jsonl(tmp_path / "d.jsonl", [json.dumps(ROW), "", "not json at all: SECRET-LINE"])

    report = client.get("/api/training/datasets/inspect", query_string={"path": data}).get_json()["data"]

    assert report["kind"] == "file" and report["trainable"] is True
    assert report["rows"] == 1 and report["usable"] == 1
    assert report["formats"] == {"alpaca": 1}
    assert report["samples"][0]["messages"][1]["content"] == "Blue."
    assert report["errors"] == ["d.jsonl line 3: not valid JSON"]
    assert "SECRET-LINE" not in json.dumps(report)


def test_inspect_of_a_url_or_nothing_says_why(client):
    url = client.get("/api/training/datasets/inspect", query_string={"path": "https://x.test/d.jsonl"})
    empty = client.get("/api/training/datasets/inspect")
    assert url.status_code == 200 and "is a URL" in url.get_json()["data"]["reason"]
    assert empty.get_json()["data"]["trainable"] is False


def test_locations_name_the_datasets_folder_and_home_with_whether_they_exist(client, tmp_path, monkeypatch):
    from backend.api.training import routes

    monkeypatch.setattr(routes, "training_datasets_dir", lambda: tmp_path / "datasets")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    missing = client.get("/api/training/datasets/locations").get_json()["data"]
    assert [(loc["id"], loc["exists"]) for loc in missing["locations"]] == [("datasets", False), ("home", True)]
    assert missing["default"] == str(tmp_path / "home")

    (tmp_path / "datasets").mkdir()
    present = client.get("/api/training/datasets/locations").get_json()["data"]
    assert present["default"] == str(tmp_path / "datasets")


def test_the_dataset_routes_are_closed_to_other_hosts_like_the_folder_browser():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    for path in ("/api/training/datasets/inspect", "/api/training/datasets/locations",
                 "/api/files/browse-server"):
        with app.test_request_context(path, method="GET"):
            assert _is_protected() is True, path
    with app.test_request_context("/api/training/jobs", method="GET"):
        assert _is_protected() is False


# ---- saving a dataset -----------------------------------------------------------

def test_a_trainable_file_is_saved_with_its_resolved_path(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    data = _jsonl(tmp_path / "sets" / "d.jsonl", [json.dumps(ROW)])

    response = client.post("/api/training_datasets/", json={"name": " notes ", "path": "~/sets/d.jsonl"})

    assert response.status_code == 201
    saved = response.get_json()
    assert saved["name"] == "notes" and saved["path"] == data


@pytest.mark.parametrize("body, says", [
    ({"name": "x", "path": "https://x.test/d.jsonl"}, "is a URL"),
    ({"name": "x"}, "Dataset path is required"),
    ({"name": "x", "path": 5}, "Dataset path is required"),
    ({"name": 5, "path": "/tmp"}, "name must be text"),
    ({"name": "  ", "path": "/tmp"}, "Dataset name is required"),
    ({"name": "x", "path": "/tmp", "description": ["a"]}, "description must be text"),
])
def test_a_dataset_the_trainer_cannot_use_is_refused_with_a_reason(client, body, says):
    response = client.post("/api/training_datasets/", json=body)
    assert response.status_code == 400
    assert says in response.get_json()["error"]
    assert db.session.query(TrainingDataset).count() == 0


def test_an_untrainable_file_is_refused_without_quoting_it(client, tmp_path):
    data = _jsonl(tmp_path / "chat.jsonl", ['{"messages": [{"role": "user", "content": "PRIVATE-TEXT"}]}'])
    response = client.post("/api/training_datasets/", json={"name": "x", "path": data})
    assert response.status_code == 400
    message = response.get_json()["error"]
    assert "cannot be trained on" in message and "no assistant turn" in message
    assert "PRIVATE-TEXT" not in message


def test_a_request_that_is_not_an_object_is_refused(client):
    response = client.post("/api/training_datasets/", data="[1]", content_type="application/json")
    assert response.status_code == 400


def test_an_old_path_is_kept_on_rename_and_a_new_path_is_checked(client, tmp_path):
    legacy = TrainingDataset(name="old", path="https://x.test/d.jsonl")
    db.session.add(legacy)
    db.session.commit()

    renamed = client.put(f"/api/training_datasets/{legacy.id}",
                         json={"name": "renamed", "path": "https://x.test/d.jsonl"})
    assert renamed.status_code == 200 and renamed.get_json()["name"] == "renamed"

    bad = client.put(f"/api/training_datasets/{legacy.id}", json={"path": str(tmp_path / "missing.jsonl")})
    assert bad.status_code == 400 and "does not exist" in bad.get_json()["error"]

    good = _jsonl(tmp_path / "d.jsonl", [json.dumps(ROW)])
    moved = client.put(f"/api/training_datasets/{legacy.id}", json={"path": good})
    assert moved.status_code == 200 and moved.get_json()["path"] == good


def test_a_name_that_is_not_text_is_a_400_not_a_500(client):
    ds = TrainingDataset(name="old", path="/x")
    db.session.add(ds)
    db.session.commit()
    response = client.put(f"/api/training_datasets/{ds.id}", json={"name": 7})
    assert response.status_code == 400
