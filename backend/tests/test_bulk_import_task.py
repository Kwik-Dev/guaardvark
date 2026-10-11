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
                               "errors": 0, "queued_for_indexing": 2}
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
