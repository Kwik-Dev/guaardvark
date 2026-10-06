"""CLI music-video / film-crew talk HTTP and never POST approve."""
import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from llx.client import LlxError
from llx.main import app

runner = CliRunner()


class _FakeClient:
    server_url = "http://localhost:5002"

    def __init__(self):
        self.posts = []
        self.gets = []
        self.deletes = []
        self.uploads = []

    def get(self, endpoint, **params):
        self.gets.append(endpoint)
        if endpoint == "/api/music-video":
            return {"music_videos": [{"id": 1, "name": "Night", "current_stage": "analyzing", "status": "analyzing", "clips_done": 0, "clip_count": 0}]}
        if endpoint.startswith("/api/music-video/"):
            return {"id": 1, "name": "Night", "current_stage": "awaiting_approval", "status": "awaiting_approval", "i2v_model": "wan22-5b", "cut_count": 4, "clips_done": 0, "clip_count": 4}
        if endpoint == "/api/production":
            return {"productions": [{"id": 2, "name": "Kettle", "current_stage": "screenwriting", "status": "screenwriting"}]}
        if endpoint.startswith("/api/production/"):
            return {"id": 2, "name": "Kettle", "current_stage": "screenwriting", "status": "screenwriting", "shots": [], "settings_json": {"video_model": "wan22-5b"}}
        return {}

    def post(self, path, json=None, **kwargs):
        self.posts.append((path, json))
        if path == "/api/music-video":
            return {"id": 1, "name": json.get("name"), "current_stage": "analyzing", "status": "analyzing"}
        if path == "/api/production":
            return {"id": 2, "name": json.get("name"), "current_stage": "screenwriting", "status": "screenwriting"}
        if path.endswith("/cancel"):
            return {"id": 1, "current_stage": "cancelled", "status": "cancelled"}
        return {}

    def delete(self, path):
        self.deletes.append(path)
        return {"deleted": 1}

    def upload(self, path, file_path, **extra):
        self.uploads.append((path, str(file_path), extra))
        return {"id": 99, "filename": file_path.name}


def test_music_video_create_uses_document_id_and_does_not_approve():
    fake = _FakeClient()
    with patch("llx.commands.music_video.get_client", return_value=fake), \
         patch("llx.commands.music_video.get_global_server", return_value="http://localhost:5002"), \
         patch("llx.commands.music_video.get_global_json", return_value=True):
        result = runner.invoke(app, [
            "music-video", "create", "--song", "12", "--style", "neon noir", "--json",
        ])
    assert result.exit_code == 0, result.output
    assert fake.posts
    path, body = fake.posts[0]
    assert path == "/api/music-video"
    assert body["song_document_id"] == 12
    assert body["style_prompt"] == "neon noir"
    assert not any(p.endswith("/approve") for p, _ in fake.posts)
    assert fake.uploads == []


def test_music_video_create_uploads_local_song(tmp_path):
    song = tmp_path / "hook.mp3"
    song.write_bytes(b"ID3")
    fake = _FakeClient()
    with patch("llx.commands.music_video.get_client", return_value=fake), \
         patch("llx.commands.music_video.get_global_server", return_value="http://localhost:5002"), \
         patch("llx.commands.music_video.get_global_json", return_value=True):
        result = runner.invoke(app, [
            "music-video", "create", "--song", str(song), "--style", "wet asphalt", "--json",
        ])
    assert result.exit_code == 0, result.output
    assert fake.uploads and fake.uploads[0][0] == "/api/files/upload"
    assert fake.posts[0][1]["song_document_id"] == 99
    assert not any(p.endswith("/approve") for p, _ in fake.posts)


def test_film_crew_create_posts_script_and_does_not_render():
    fake = _FakeClient()
    with patch("llx.commands.film_crew.get_client", return_value=fake), \
         patch("llx.commands.film_crew.get_global_server", return_value="http://localhost:5002"), \
         patch("llx.commands.film_crew.get_global_json", return_value=True):
        result = runner.invoke(app, [
            "film-crew", "create", "--script", "INT. ROOM. Hi.", "--json",
        ])
    assert result.exit_code == 0, result.output
    path, body = fake.posts[0]
    assert path == "/api/production"
    assert "INT. ROOM" in body["script_text"]
    assert not any("approve" in p or "render" in p for p, _ in fake.posts)


def test_music_video_list_and_cancel():
    fake = _FakeClient()
    with patch("llx.commands.music_video.get_client", return_value=fake), \
         patch("llx.commands.music_video.get_global_server", return_value="http://localhost:5002"), \
         patch("llx.commands.music_video.get_global_json", return_value=True):
        listed = runner.invoke(app, ["music-video", "list", "--json"])
        cancelled = runner.invoke(app, ["music-video", "cancel", "1", "--json"])
    assert listed.exit_code == 0, listed.output
    assert cancelled.exit_code == 0, cancelled.output
    assert "/api/music-video" in fake.gets
    assert any(p.endswith("/cancel") for p, _ in fake.posts)


# --- issue #7: `list` resolves the output Document ---------------------------


class _OutputFakeClient(_FakeClient):
    """The list row carries a real `output_document_id`; the render is a Document."""

    def doc_get(self, endpoint):
        return {"data": {"filename": "arrangement_abc.mp4", "path": "Videos/arrangement_abc.mp4"}}

    def get(self, endpoint, **params):
        if endpoint == "/api/music-video":
            return {"music_videos": [{
                "id": 7, "name": "Neon", "status": "complete", "current_stage": "complete",
                "cut_count": 3, "clips_done": 3, "clip_count": 3,
                "output_document_id": 7, "song_document_id": 12,
                "clips": [{"index": 0, "clip_path": "/out/cut0.mp4", "prompt": "wide"}],
                "cut_plan": [{"index": 0, "start_s": 0.0, "end_s": 1.0}],
            }]}
        if endpoint.startswith("/api/files/document/"):
            return self.doc_get(endpoint)
        return super().get(endpoint, **params)


def _invoke_list(client, *extra):
    with patch("llx.commands.music_video.get_client", return_value=client), \
         patch("llx.commands.music_video.get_global_server", return_value="http://localhost:5002"), \
         patch("llx.commands.music_video.get_global_json", return_value=True):
        return runner.invoke(app, ["music-video", "list", *extra, "--json"])


def test_music_video_list_resolves_the_output_document():
    result = _invoke_list(_OutputFakeClient())
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)[0]
    assert row["output_document_id"] == 7
    assert row["output_filename"] == "arrangement_abc.mp4"
    assert row["output_path"] == "Videos/arrangement_abc.mp4"
    assert row["output_url"] == "/api/files/document/7/download"
    # The list row is a summary: the per-cut payload belongs to `clips`/`cuts`.
    assert "clips" not in row and "cut_plan" not in row


def test_music_video_list_tolerates_a_missing_output_document():
    class _MissingDoc(_OutputFakeClient):
        def doc_get(self, endpoint):
            raise LlxError("Not Found", 404)

    result = _invoke_list(_MissingDoc())
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)[0]
    assert row["output_document_id"] == 7
    assert row["output_filename"] == ""
    assert row["output_url"] == "/api/files/document/7/download"


def test_music_video_list_full_keeps_the_raw_records():
    result = _invoke_list(_OutputFakeClient(), "--full")
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)[0]
    assert "clips" in row and "cut_plan" in row
    assert "output_filename" not in row
