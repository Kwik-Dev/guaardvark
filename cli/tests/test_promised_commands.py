"""Commands that used to fail against the real backend: jobs, rag, search, audio
music, swarm run, lessons, outreach subcommands, /ingest folder mapping."""

import json

import pytest
from typer.testing import CliRunner

from llx.client import LlxError
from llx.job_status import read_job
from llx.main import app

runner = CliRunner()


class _Client:
    """Answers the routes a test names; anything else is a 404 like the backend."""

    server_url = "http://localhost:5000"

    def __init__(self, routes=None, posts=None):
        self.routes = routes or {}
        self.posts = posts or {}
        self.sent = []

    def get(self, endpoint, **params):
        self.sent.append(("GET", endpoint, params))
        if endpoint in self.routes:
            return self.routes[endpoint]
        raise LlxError("not found", 404)

    def post(self, path, json=None, **kwargs):
        self.sent.append(("POST", path, json))
        answer = self.posts.get(path)
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else {"success": True, "data": {}}

    def execute_tool(self, name, parameters=None):
        self.sent.append(("TOOL", name, parameters))
        return self.posts[f"tool:{name}"]

    def upload(self, path, file_path, **fields):
        self.sent.append(("UPLOAD", file_path.name, fields))
        return {"data": {"id": len(self.sent)}}


# ── jobs ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("job_id,route", [
    ("ImageBatch_09-28-2026_1", "/api/batch-image/status/ImageBatch_09-28-2026_1"),
    ("VideoBatch_09-28-2026_1", "/api/batch-video/status/VideoBatch_09-28-2026_1"),
    ("bulk_gen_1_ab", "/api/bulk-generate/status/bulk_gen_1_ab"),
    ("0123456789abcdef0123456789abcdef", "/api/audio-foundry/jobs/0123456789abcdef0123456789abcdef"),
    ("42", "/api/jobs/task:42"),
    ("video_gen:VideoBatch_x", "/api/jobs/video_gen:VideoBatch_x"),
])
def test_read_job_picks_the_route_for_each_id(job_id, route):
    # Bulk jobs nest their state under progress_status, as the real route does.
    client = _Client(routes={route: {"status": "running", "progress_status": {"status": "running"}}})
    info = read_job(client, job_id)
    assert info["id"] == job_id
    assert info["status"] == "running"
    assert client.sent[0][1] == route


def test_read_job_normalises_finished_image_batch():
    client = _Client(routes={"/api/batch-image/status/ImageBatch_1": {"data": {
        "status": "completed", "total_images": 2, "completed_images": 2, "progress_percentage": 100,
        "results": [{"success": True, "image_path": "/x/images/a.png"}]}}})
    info = read_job(client, "ImageBatch_1")
    assert info["status"] == "completed"
    assert info["percent"] == 100
    assert info["files"] == ["/api/batch-image/image/ImageBatch_1/a.png"]


def test_read_job_unknown_id_is_an_error():
    with pytest.raises(LlxError):
        read_job(_Client(), "nothing-like-this")


def test_jobs_watch_follows_until_done(monkeypatch):
    states = iter(["running", "running", "done"])
    client = _Client()
    client.get = lambda endpoint, **p: {"status": next(states), "progress": {"current": 1, "total": 2},
                                        "result": {"path": "/x/song.wav"}}
    monkeypatch.setattr("llx.commands.jobs.get_client", lambda server=None: client)
    monkeypatch.setattr("llx.commands.jobs.time.sleep", lambda s: None)
    result = runner.invoke(app, ["jobs", "watch", "0123456789abcdef0123456789abcdef"])
    assert result.exit_code == 0, result.output
    assert "Complete" in result.output


def test_jobs_status_accepts_string_ids(monkeypatch):
    client = _Client(routes={"/api/batch-video/status/VideoBatch_1": {"status": "queued"}})
    monkeypatch.setattr("llx.commands.jobs.get_client", lambda server=None: client)
    result = runner.invoke(app, ["jobs", "status", "VideoBatch_1", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["status"] == "pending"


# ── search / rag ────────────────────────────────────────────────────────

_SEARCH = {"tool:search_knowledge_base": {"success": True, "result": {"success": True, "metadata": {
    "retrieval": {"returned": 1},
    "results": [{"text": "The Q3 launch moved to October.", "score": 0.82,
                 "metadata": {"source_filename": "acme_notes.pdf", "page_label": "2"}}]}}}}


def test_search_shows_real_scores(monkeypatch):
    client = _Client(posts=_SEARCH)
    monkeypatch.setattr("llx.commands.search.get_client", lambda server=None: client)
    result = runner.invoke(app, ["search", "launch date", "--json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)["data"]["results"][0]
    assert row["score"] == 0.82 and row["source"] == "acme_notes.pdf"


def test_rag_query_uses_the_knowledge_search(monkeypatch):
    client = _Client(posts=_SEARCH)
    monkeypatch.setattr("llx.commands.rag.get_client", lambda server=None: client)
    result = runner.invoke(app, ["rag", "query", "launch date", "-k", "3", "--json"])
    assert result.exit_code == 0, result.output
    assert ("TOOL", "search_knowledge_base", {"query": "launch date", "top_k": 3}) in client.sent


def test_rag_status_counts_documents(monkeypatch):
    client = _Client(routes={"/api/meta/index-info": {"embedding_model": "embeddinggemma"}}, posts={
        "tool:list_documents": {"success": True, "result": {"success": True, "metadata": {"total": 2},
            "output": "KNOWLEDGE BASE — 2 document(s) indexed · showing 1-2\n"
                      "  a.pdf — 12 passages [docling]\n  b.md — 3 passages [text]"}}})
    monkeypatch.setattr("llx.commands.rag.get_client", lambda server=None: client)
    result = runner.invoke(app, ["rag", "status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["documents"] == 2 and data["passages_listed"] == 15
    assert data["embedding_model"] == "embeddinggemma"


# ── audio music ─────────────────────────────────────────────────────────


def test_audio_music_sends_style_prompt_async(monkeypatch):
    client = _Client(posts={"/api/audio-foundry/generate/music": {"job_id": "f" * 32, "status": "queued"}})
    monkeypatch.setattr("llx.commands.audio.get_client", lambda server=None: client)
    result = runner.invoke(app, ["audio", "music", "lo-fi piano, 80 bpm", "--seconds", "20", "--json"])
    assert result.exit_code == 0, result.output
    body = client.sent[0][2]
    assert body["style_prompt"] == "lo-fi piano, 80 bpm"
    assert body["duration_s"] == 20 and body["async"] is True
    assert "prompt" not in body


# ── swarm run ───────────────────────────────────────────────────────────


def test_swarm_run_sends_a_sentence_as_prompt(monkeypatch):
    client = _Client(routes={"/api/swarm/templates": {"data": {"templates": []}}},
                     posts={"/api/swarm/launch": {"data": {"swarm_id": "s1"}}})
    monkeypatch.setattr("llx.commands.swarm.get_client", lambda server=None: client)
    result = runner.invoke(app, ["swarm", "run", "fix the failing tests", "--json"])
    assert result.exit_code == 0, result.output
    launch = [s for s in client.sent if s[1] == "/api/swarm/launch"][0][2]
    assert launch["prompt"] == "fix the failing tests"


def test_swarm_run_by_template_name(monkeypatch):
    client = _Client(routes={"/api/swarm/templates": {"data": {"templates": [{"filename": "test-coverage.md"}]}}},
                     posts={"/api/swarm/launch": {"data": {"swarm_id": "s1"}}})
    monkeypatch.setattr("llx.commands.swarm.get_client", lambda server=None: client)
    result = runner.invoke(app, ["swarm", "run", "test-coverage", "--json"])
    assert result.exit_code == 0, result.output
    launch = [s for s in client.sent if s[1] == "/api/swarm/launch"][0][2]
    assert launch["plan_path"] == "plugins/swarm/templates/test-coverage.md"


def test_swarm_run_dirty_tree_says_what_to_do(monkeypatch):
    client = _Client(routes={"/api/swarm/templates": {"data": {"templates": []}}},
                     posts={"/api/swarm/launch": LlxError("dirty", 409)})
    monkeypatch.setattr("llx.commands.swarm.get_client", lambda server=None: client)
    result = runner.invoke(app, ["swarm", "run", "tidy the README"])
    assert result.exit_code == 1
    assert "--allow-dirty" in result.output


# ── lessons ─────────────────────────────────────────────────────────────


def test_lessons_begin_uses_the_latest_chat(monkeypatch):
    client = _Client(posts={"/api/lessons/start": {"success": True, "lesson_id": "L1"}})
    monkeypatch.setattr("llx.commands.lessons.get_client", lambda server=None: client)
    monkeypatch.setattr("llx.config.get_last_session_id", lambda: "chat-123")
    result = runner.invoke(app, ["lessons", "begin"])
    assert result.exit_code == 0, result.output
    assert client.sent[0][2]["session_id"] == "chat-123"


# ── outreach ────────────────────────────────────────────────────────────


def test_outreach_subcommands_are_reachable(monkeypatch):
    client = _Client(routes={"/api/social-outreach/queue": []})
    monkeypatch.setattr("llx.commands.outreach.get_client", lambda server=None: client)
    result = runner.invoke(app, ["outreach", "queue"])
    assert result.exit_code == 0, result.output
    assert client.sent[0][1] == "/api/social-outreach/queue"


def test_outreach_sentence_goes_to_intent(monkeypatch):
    client = _Client(posts={"/api/social-outreach/intent": {"ok": True, "message": "drafted"}})
    monkeypatch.setattr("llx.commands.outreach.get_client", lambda server=None: client)
    result = runner.invoke(app, ["outreach", "find threads about local AI"])
    assert result.exit_code == 0, result.output
    assert client.sent[0][2]["text"] == "find threads about local AI"


# ── /ingest ─────────────────────────────────────────────────────────────


def test_ingest_folder_keeps_its_layout(tmp_path):
    from llx.kb import ingest_path

    root = tmp_path / "research"
    (root / "q3").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "notes.md").write_text("n")
    (root / "q3" / "plan.md").write_text("p")
    (root / ".git" / "config").write_text("hidden")

    client = _Client()
    done = ingest_path(client, root)
    uploads = {s[1]: s[2]["folder_path"] for s in client.sent if s[0] == "UPLOAD"}
    assert uploads == {"notes.md": "research", "plan.md": "research/q3"}
    folders = [s[2] for s in client.sent if s[1] == "/api/files/folder"]
    assert {"name": "research", "parent_path": ""} in folders
    assert {"name": "q3", "parent_path": "research"} in folders
    assert all("id" in d for d in done)
