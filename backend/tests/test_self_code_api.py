from flask import Flask
import pytest


def _app():
    pytest.importorskip("sqlalchemy")
    from backend.api.files_api import files_bp
    from backend.api.self_code_api import self_code_bp
    from backend.models import db

    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        UPLOAD_FOLDER="/tmp/guaardvark-test-uploads",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(files_bp)
    app.register_blueprint(self_code_bp)
    with app.app_context():
        db.create_all()
    return app


def test_files_browse_exposes_live_repo_mount(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    with _app().test_client() as client:
        response = client.get("/api/files/browse?path=/&fields=light")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert any(folder["path"] == "/__repo__" for folder in data["folders"])


def test_files_browse_live_repo_reads_worktree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "backend" / "app.py").write_text("print('ok')\n")
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    with _app().test_client() as client:
        response = client.get("/api/files/browse?path=/__repo__/backend&fields=light")

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["path"] == "/__repo__/backend"
    assert data["documents"][0]["relative_path"] == "backend/app.py"


def test_self_code_file_rejects_path_traversal(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("GUAARDVARK_ROOT", str(repo))

    with _app().test_client() as client:
        response = client.get("/api/self-code/file?path=../outside.py")

    assert response.status_code == 403
