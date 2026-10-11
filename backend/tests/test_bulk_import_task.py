"""bulk_import_documents_task: the source file is only removed after its row commits.

The task talks to the database through plain SQL on a session from
get_db_session(), so a small fake session stands in for Postgres; the files
are real, in a temp tree. Redis is faked so the status writes go nowhere.
"""
import os
import sys
import types

import pytest

from backend import celery_tasks_isolated as cti


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeSession:
    """Just enough SQL for the task: folders and documents by path."""

    def __init__(self, fail_insert_for=()):
        self.folders = {}      # path -> id
        self.documents = {}    # path -> (id, index_status)
        self._pending_doc = None
        self._next_id = 1
        self.fail_insert_for = set(fail_insert_for)
        self.closed = 0

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}
        if sql.startswith("SELECT id, path FROM folders"):
            path = params["folder_path"]
            return _Result((self.folders[path], path) if path in self.folders else None)
        if sql.startswith("INSERT INTO folders"):
            fid = self._new_id()
            self.folders[params["path"]] = fid
            return _Result((fid,))
        if sql.startswith("SELECT id FROM folders"):
            fid = self.folders.get(params["p"])
            return _Result((fid,) if fid else None)
        if sql.startswith("SELECT id, index_status FROM documents"):
            return _Result(self.documents.get(params["db_path"]))
        if sql.startswith("INSERT INTO documents"):
            if params["filename"] in self.fail_insert_for:
                raise RuntimeError(f"insert refused for {params['filename']}")
            did = self._new_id()
            self._pending_doc = (params["path"], (did, params["index_status"]))
            return _Result((did,))
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        if self._pending_doc:
            path, row = self._pending_doc
            self.documents[path] = row
            self._pending_doc = None

    def rollback(self):
        self._pending_doc = None

    def close(self):
        self.closed += 1


class _Queued:
    def __init__(self):
        self.calls = []

    def apply_async(self, args, queue=None):
        self.calls.append((args, queue))
        return types.SimpleNamespace(id="t")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "notes.md").write_text("# notes\n")
    (source / "sub" / "deep.txt").write_text("deep\n")
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", str(uploads))

    fake_redis = types.ModuleType("redis")
    fake_redis.Redis = types.SimpleNamespace(
        from_url=lambda *a, **k: types.SimpleNamespace(setex=lambda *a, **k: None)
    )
    monkeypatch.setitem(sys.modules, "redis", fake_redis)

    queued = _Queued()
    monkeypatch.setattr(cti, "index_document_task", queued, raising=False)

    def run(session, **kw):
        monkeypatch.setattr(cti, "get_db_session", lambda: session)
        args = dict(job_id="job", source_path=str(source), target_folder="Imports",
                    reindex_missing=True, force_copy=False, dry_run=False)
        args.update(kw)
        return cti.bulk_import_documents_task(**args)

    return types.SimpleNamespace(uploads=uploads, source=source, run=run, queued=queued)


def test_move_removes_the_source_only_after_the_row_commits(harness):
    session = FakeSession()
    result = harness.run(session)

    assert result["stats"] == {"total_files": 2, "imported": 2, "skipped": 0,
                               "errors": 0, "queued_for_indexing": 2,
                               "skipped_private": {"hidden_dirs": 0, "hidden_files": 0,
                                                   "sensitive_files": 0, "symlinks": 0,
                                                   "internal_dirs": 0}}
    assert not (harness.source / "notes.md").exists()
    assert not (harness.source / "sub" / "deep.txt").exists()
    assert (harness.uploads / "Imports" / "notes.md").read_text() == "# notes\n"
    assert (harness.uploads / "Imports" / "sub" / "deep.txt").read_text() == "deep\n"
    assert set(session.documents) == {"Imports/notes.md", "Imports/sub/deep.txt"}
    assert len(harness.queued.calls) == 2


def test_failed_insert_leaves_the_source_alone_and_no_stray_copy(harness):
    session = FakeSession(fail_insert_for={"notes.md"})
    result = harness.run(session)

    assert result["stats"]["errors"] == 1
    assert result["stats"]["imported"] == 1
    # notes.md: still at home, nothing under uploads, no row
    assert (harness.source / "notes.md").read_text() == "# notes\n"
    assert not (harness.uploads / "Imports" / "notes.md").exists()
    assert "Imports/notes.md" not in session.documents
    # deep.txt went through as usual
    assert not (harness.source / "sub" / "deep.txt").exists()
    assert "Imports/sub/deep.txt" in session.documents


def test_copy_mode_keeps_the_source(harness):
    session = FakeSession()
    harness.run(session, force_copy=True)
    assert (harness.source / "notes.md").exists()
    assert (harness.uploads / "Imports" / "notes.md").exists()


def test_rerun_skips_already_imported_files_without_a_suffixed_duplicate(harness):
    session = FakeSession()
    harness.run(session, force_copy=True)
    assert len(session.documents) == 2
    harness.queued.calls.clear()
    # The first import never finished indexing one file.
    session.documents["Imports/notes.md"] = (session.documents["Imports/notes.md"][0], "PENDING")
    session.documents["Imports/sub/deep.txt"] = (session.documents["Imports/sub/deep.txt"][0], "INDEXED")

    result = harness.run(session, force_copy=False)

    assert result["stats"]["imported"] == 0
    assert result["stats"]["skipped"] == 2
    assert not os.path.exists(harness.uploads / "Imports" / "notes_1.md")
    assert (harness.source / "notes.md").exists(), "a skipped file is not moved"
    assert set(session.documents) == {"Imports/notes.md", "Imports/sub/deep.txt"}
    # Only the unindexed one is queued again.
    assert [a for a, _ in harness.queued.calls] == [(session.documents["Imports/notes.md"][0],)]


# --------------------------------------------------------------------------
# The walk: hidden folders and files, credentials, symlinks and Guaardvark's
# own data never leave the source.
# --------------------------------------------------------------------------

def _project_tree(root):
    """A code project as a user would point the import at."""
    (root / ".git" / "objects").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / ".git" / "config").write_text("[core]\n")
    (root / ".git" / "objects" / "pack").write_text("x")
    (root / ".ssh").mkdir()
    (root / ".ssh" / "id_rsa").write_text("PRIVATE\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / "README").write_text("readme\n")
    (root / "notes.md").write_text("# notes\n")
    return root


@pytest.fixture
def project(tmp_path, monkeypatch, harness):
    src = _project_tree(tmp_path / "project")
    return types.SimpleNamespace(src=src, harness=harness)


def test_import_takes_readme_and_notes_only(project):
    session = FakeSession()
    result = project.harness.run(session, source_path=str(project.src), force_copy=False)

    assert result["stats"]["imported"] == 2
    assert set(session.documents) == {"Imports/README", "Imports/notes.md"}
    # The repository and the keys are exactly where they were.
    assert (project.src / ".git" / "HEAD").read_text() == "ref: refs/heads/main\n"
    assert (project.src / ".git" / "objects" / "pack").exists()
    assert (project.src / ".ssh" / "id_rsa").read_text() == "PRIVATE\n"
    assert (project.src / ".env").read_text() == "SECRET=1\n"
    assert not (project.src / "README").exists(), "the plain file was moved as asked"
    assert not list(project.harness.uploads.rglob("id_rsa"))
    assert not list(project.harness.uploads.rglob("HEAD"))
    assert result["stats"]["skipped_private"] == {
        "hidden_dirs": 2, "hidden_files": 1, "sensitive_files": 0, "symlinks": 0, "internal_dirs": 0,
    }


def test_dry_run_reports_what_the_walk_skips(project):
    result = project.harness.run(FakeSession(), source_path=str(project.src), dry_run=True)
    assert sorted(result["files"]) == ["README", "notes.md"]
    assert result["skipped_private"]["hidden_dirs"] == 2
    assert result["skipped_private"]["hidden_files"] == 1


def test_scan_rules_credentials_symlinks_and_guaardvark_data(tmp_path):
    src = tmp_path / "home"
    src.mkdir()
    (src / "credentials").write_text("aws\n")
    (src / "server.key").write_text("key\n")
    (src / "cert.pem").write_text("pem\n")
    (src / ".git-credentials").write_text("token\n")
    (src / "Makefile").write_text("all:\n")
    (src / "LICENSE").write_text("MIT\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n")
    os.symlink(outside, src / "link.txt")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "leak.txt").write_text("leak\n")
    os.symlink(outside_dir, src / "linked_dir")
    # Guaardvark's own data folder lives under the home folder being imported.
    data = src / "GX" / "data"
    (data / "config").mkdir(parents=True)
    (data / "docstore.json").write_text("{}")
    (data / "config" / "settings.json").write_text("{}")
    (data / ".swarm_internal_secret").write_text("s")
    (data / "uploads").mkdir()
    (data / "uploads" / "old.txt").write_text("already imported\n")
    (src / "GX" / "app.py").write_text("print()\n")

    files, skipped = cti.scan_import_source(
        str(src), {".txt", ".json", ".py"}, str(data), str(data / "uploads"), None)

    assert sorted(f[2] for f in files) == ["LICENSE", "Makefile", "app.py"]
    assert skipped["sensitive_files"] == 3            # credentials, server.key, cert.pem
    assert skipped["hidden_files"] == 1               # .git-credentials (hidden; also sensitive)
    assert skipped["symlinks"] == 2                   # link.txt, linked_dir
    assert skipped["internal_dirs"] == 1              # GX/data as a whole


def test_scan_never_enters_the_target_folder(tmp_path):
    src = tmp_path / "src"
    (src / "Imports").mkdir(parents=True)
    (src / "Imports" / "earlier.txt").write_text("x")
    (src / "new.txt").write_text("y")
    files, skipped = cti.scan_import_source(
        str(src), {".txt"}, None, None, str(src / "Imports"))
    assert [f[2] for f in files] == ["new.txt"]
    assert skipped["internal_dirs"] == 1


def test_source_inside_guaardvark_data_is_refused_except_uploads(tmp_path, monkeypatch, harness):
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "settings.json").write_text("{}")
    (data / "uploads" / "old").mkdir(parents=True)
    (data / "uploads" / "old" / "a.txt").write_text("a")
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(data))
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", str(data / "uploads"))

    refused = harness.run(FakeSession(), source_path=str(data / "config"), dry_run=True)
    assert "inside Guaardvark's own data folder" in refused["error"]
    assert (data / "config" / "settings.json").exists()

    allowed = harness.run(FakeSession(), source_path=str(data / "uploads" / "old"), dry_run=True)
    assert allowed["files"] == ["a.txt"]


def test_copy_is_the_default_on_the_task_and_the_route():
    import inspect
    assert inspect.signature(cti.bulk_import_documents_task).parameters["force_copy"].default is True
    from backend.api import files_api
    src = inspect.getsource(files_api.start_bulk_import)
    assert 'data.get("force_copy", True)' in src


def test_scan_prunes_guaardvark_backups_under_a_parent_import(tmp_path):
    # Importing the install's parent folder must not walk into backups/ (full install zips).
    root = tmp_path / "install"
    (root / "backups").mkdir(parents=True)
    (root / "backups" / "full-2026.zip").write_text("zip")
    (root / "notes.txt").write_text("keep")
    files, skipped = cti.scan_import_source(
        str(root), {".txt", ".zip"}, None, None, None, str(root / "backups"))
    assert [f[2] for f in files] == ["notes.txt"]
    assert skipped["internal_dirs"] == 1


def test_storage_dirs_resolve_like_backend_config(tmp_path, monkeypatch):
    # Relative values resolve against GUAARDVARK_ROOT with config's defaults, so the
    # worker and the routes agree on where data, uploads and backups live.
    monkeypatch.setenv("GUAARDVARK_ROOT", str(tmp_path))
    for var in ("GUAARDVARK_STORAGE_DIR", "GUAARDVARK_UPLOAD_DIR", "GUAARDVARK_BACKUP_DIR"):
        monkeypatch.delenv(var, raising=False)
    assert cti._guaardvark_storage_dirs() == (
        str(tmp_path / "data"), str(tmp_path / "data/uploads"), str(tmp_path / "backups"))
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", "elsewhere/data")
    monkeypatch.setenv("GUAARDVARK_BACKUP_DIR", "/abs/backups")
    storage, upload, backup = cti._guaardvark_storage_dirs()
    assert storage == str(tmp_path / "elsewhere/data")
    assert upload == str(tmp_path / "data/uploads")
    assert backup == "/abs/backups"


def test_source_inside_guaardvark_backups_is_refused(tmp_path, monkeypatch, harness):
    backups = tmp_path / "backups"
    (backups / "old").mkdir(parents=True)
    (backups / "old" / "dump.txt").write_text("d")
    monkeypatch.setenv("GUAARDVARK_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GUAARDVARK_UPLOAD_DIR", str(tmp_path / "data" / "uploads"))
    monkeypatch.setenv("GUAARDVARK_BACKUP_DIR", str(backups))
    refused = harness.run(FakeSession(), source_path=str(backups / "old"), dry_run=True)
    assert "inside Guaardvark's backups folder" in refused["error"]
    assert (backups / "old" / "dump.txt").exists()
