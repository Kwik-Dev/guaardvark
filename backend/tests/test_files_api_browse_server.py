"""GET /api/files/browse-server and POST /api/files/bulk-import stay inside the
Bulk Import roots.

The picker used to list any readable folder on the server (/etc, ~/.ssh) and
the import route queued any path. Both now resolve symlinks and refuse
anything outside the home folder, Guaardvark's data folder and
GUAARDVARK_ALLOWED_PATHS, and never list hidden entries.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask

from backend.api import files_api
from backend.api.files_api import files_bp
from backend.models import db


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """home/ (with Docs/, Docs/Q1/, .ssh/, a symlink out) and data/, plus an
    outside/ folder that is not a root."""
    home = tmp_path / "home"
    data = tmp_path / "data"
    outside = tmp_path / "outside"
    (home / "Docs" / "Q1").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / "Docs" / "notes.txt").write_text("x")
    (home / "Docs" / "id_rsa").write_text("x")
    (home / "Docs" / ".hidden.txt").write_text("x")
    data.mkdir()
    outside.mkdir()
    (home / "escape").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(files_api, "bulk_import_roots", lambda: [str(home), str(data)])
    return types.SimpleNamespace(home=home, data=data, outside=outside)


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
    })
    db.init_app(app)
    app.register_blueprint(files_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _browse(client, path=None, **params):
    if path is not None:
        params["path"] = path
    return client.get("/api/files/browse-server", query_string=params)


def test_no_path_lists_home_with_no_parent(client, roots):
    r = _browse(client)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["path"] == os.path.realpath(str(roots.home))
    assert body["parent_path"] is None
    # .ssh is hidden; escape is a symlink to a folder outside the roots.
    assert [d["name"] for d in body["directories"]] == ["Docs"]
    assert body["roots"] == [str(roots.home), str(roots.data)]


def test_subfolder_lists_and_offers_parent_until_the_root(client, roots):
    r = _browse(client, str(roots.home / "Docs"), include_files="true")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert [d["name"] for d in body["directories"]] == ["Q1"]
    assert body["parent_path"] == os.path.realpath(str(roots.home))
    names = [f["name"] for f in body["files"]]
    assert "notes.txt" in names
    assert ".hidden.txt" not in names
    assert "id_rsa" not in names, "credential files are never listed"


def test_relative_path_resolves_under_home(client, roots):
    r = _browse(client, "Docs/Q1")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["path"] == os.path.realpath(str(roots.home / "Docs" / "Q1"))


@pytest.mark.parametrize("path", ["/etc", "/", "/root"])
def test_system_paths_are_refused(client, roots, path):
    r = _browse(client, path)
    assert r.status_code == 403
    assert r.get_json()["code"] == "PATH_NOT_ALLOWED"


def test_folder_outside_the_roots_is_refused(client, roots):
    r = _browse(client, str(roots.outside))
    assert r.status_code == 403
    assert r.get_json()["code"] == "PATH_NOT_ALLOWED"


def test_symlink_out_of_home_is_refused(client, roots):
    r = _browse(client, str(roots.home / "escape"))
    assert r.status_code == 403


def test_dot_dot_cannot_climb_out(client, roots):
    r = _browse(client, str(roots.home / "Docs" / ".." / ".."))
    assert r.status_code == 403


def test_hidden_folder_under_home_is_refused(client, roots):
    r = _browse(client, str(roots.home / ".ssh"))
    assert r.status_code == 403
    assert "Hidden" in r.get_json()["error"]


def test_show_hidden_is_not_honoured(client, roots):
    r = _browse(client, str(roots.home), show_hidden="true")
    assert r.status_code == 200
    assert ".ssh" not in [d["name"] for d in r.get_json()["directories"]]


def test_missing_folder_inside_a_root_is_404(client, roots):
    r = _browse(client, str(roots.home / "nope"))
    assert r.status_code == 404


def test_refusal_names_the_roots(client, roots):
    body = _browse(client, "/etc").get_json()
    assert body["roots"] == [str(roots.home), str(roots.data)]
    assert str(roots.home) in body["error"]


def test_roots_under_a_hidden_folder_still_work(client, tmp_path, monkeypatch):
    data = tmp_path / ".local" / "share" / "guaardvark" / "data"
    (data / "uploads").mkdir(parents=True)
    monkeypatch.setattr(files_api, "bulk_import_roots", lambda: [str(tmp_path / "home"), str(data)])
    r = _browse(client, str(data))
    assert r.status_code == 200, r.get_json()
    assert [d["name"] for d in r.get_json()["directories"]] == ["uploads"]


def test_root_under_a_hidden_folder_inside_home_works_while_the_hidden_folder_does_not(
    client, tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    uploads = home / ".local" / "share" / "guaardvark" / "uploads"
    (uploads / "scans").mkdir(parents=True)
    monkeypatch.setattr(files_api, "bulk_import_roots", lambda: [str(home), str(uploads)])
    assert _browse(client, str(uploads)).status_code == 200
    assert _browse(client, str(uploads / "scans")).status_code == 200
    assert _browse(client, str(home / ".local")).status_code == 403
    assert _browse(client, str(home / ".local" / "share")).status_code == 403


def test_bulk_import_roots_reads_home_uploads_mounts_and_allowed_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv(files_api.BULK_IMPORT_ROOTS_ENV, "/srv/scans:~/extra:")
    (tmp_path / "media").mkdir()
    monkeypatch.setattr(files_api, "DRIVE_MOUNT_ROOTS", (str(tmp_path / "media"), str(tmp_path / "no-such-mount")))
    from backend import config
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path / "d"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "d" / "uploads"))
    roots = files_api.bulk_import_roots()
    assert roots == [
        str(tmp_path / "h"), str(tmp_path / "d" / "uploads"), str(tmp_path / "media"),
        "/srv/scans", str(tmp_path / "h" / "extra"),
    ]
    assert str(tmp_path / "d") not in roots, "the data folder as a whole is not importable"


class _FakeTask:
    def __init__(self):
        self.calls = []

    def apply_async(self, args, queue):
        self.calls.append((args, queue))
        return types.SimpleNamespace(id="task-1")


@pytest.fixture
def fake_task(monkeypatch):
    task = _FakeTask()
    module = types.ModuleType("backend.celery_tasks_isolated")
    module.bulk_import_documents_task = task
    monkeypatch.setitem(sys.modules, "backend.celery_tasks_isolated", module)
    monkeypatch.setattr(files_api, "USE_REDIS", False)
    monkeypatch.setattr(files_api, "_bulk_import_jobs", {}, raising=False)
    return task


def test_import_outside_the_roots_is_refused_before_queuing(client, roots, fake_task):
    r = client.post("/api/files/bulk-import", json={"source_path": "/etc"})
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "PATH_NOT_ALLOWED"
    assert fake_task.calls == []


def test_import_of_a_hidden_folder_is_refused(client, roots, fake_task):
    r = client.post("/api/files/bulk-import", json={"source_path": str(roots.home / ".ssh")})
    assert r.status_code == 403
    assert fake_task.calls == []


def test_import_through_a_symlink_out_of_home_is_refused(client, roots, fake_task):
    r = client.post("/api/files/bulk-import", json={"source_path": str(roots.home / "escape")})
    assert r.status_code == 403
    assert fake_task.calls == []


def test_import_inside_a_root_queues_the_resolved_path(client, roots, fake_task):
    r = client.post("/api/files/bulk-import",
                    json={"source_path": str(roots.home / "Docs" / ".." / "Docs")})
    assert r.status_code == 202, r.get_json()
    assert len(fake_task.calls) == 1
    args, queue = fake_task.calls[0]
    assert args[1] == os.path.realpath(str(roots.home / "Docs"))
    assert queue == "indexing"


def test_import_of_a_file_inside_a_root_is_400(client, roots, fake_task):
    r = client.post("/api/files/bulk-import", json={"source_path": str(roots.home / "Docs" / "notes.txt")})
    assert r.status_code == 400
    assert fake_task.calls == []
