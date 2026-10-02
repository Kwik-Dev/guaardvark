"""generate_music_video stores a song given by path the way the library
stores uploads, so the row it makes can be downloaded and reused.

It used to insert a row with the song's absolute path, which the document
resolver cannot serve (download 404, generate_video reference_audio "not
found"), and a new row on every call, even for a song already in the library.
Runs on in-memory SQLite with scratch upload and output folders.
"""

from __future__ import annotations

import pytest
from flask import Flask

from backend.tests._mcp_sdk import use_mcp_sdk

use_mcp_sdk()  # an MCP caller's path check imports mcp.types; see backend/tests/_mcp_sdk.py

from backend import config  # noqa: E402
from backend.models import Document, db  # noqa: E402
from backend.services import output_registration  # noqa: E402
from backend.services.document_path_resolver import resolve_document_path  # noqa: E402
from backend.tools import video_pipeline_tools as vpt  # noqa: E402

MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 16
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


@pytest.fixture
def library(monkeypatch, tmp_path):
    uploads, outputs = tmp_path / "uploads", tmp_path / "outputs"
    (uploads / "Audio").mkdir(parents=True)
    outputs.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", str(uploads))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(output_registration, "UPLOAD_DIR", str(uploads))
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      UPLOAD_FOLDER=str(uploads))
    db.init_app(app)
    with app.app_context():
        db.create_all()
        output_registration.ensure_default_folders()
        yield uploads, outputs
        db.session.remove()
        db.drop_all()


def _served(doc) -> bool:
    return resolve_document_path(doc) is not None


def test_a_song_already_in_the_library_reuses_its_row(library):
    uploads, _ = library
    (uploads / "Audio" / "take.mp3").write_bytes(MP3)
    row = Document(filename="take.mp3", path="Audio/take.mp3", type=".mp3", index_status="STORED")
    db.session.add(row)
    db.session.commit()
    for mcp in (False, True):
        doc, err = vpt._document_from_song_ref(str(uploads / "Audio" / "take.mp3"), mcp=mcp)
        assert err is None and doc.id == row.id
    assert Document.query.count() == 1


def test_a_song_in_uploads_gets_a_relative_servable_row(library):
    uploads, _ = library
    (uploads / "Audio" / "fresh.wav").write_bytes(WAV)
    doc, err = vpt._document_from_song_ref(str(uploads / "Audio" / "fresh.wav"))
    assert err is None and doc.path == "Audio/fresh.wav" and _served(doc)
    again, _ = vpt._document_from_song_ref(str(uploads / "Audio" / "fresh.wav"))
    assert again.id == doc.id


def test_a_song_elsewhere_is_copied_into_the_library_once(library):
    uploads, outputs = library
    (outputs / "audio").mkdir()
    (outputs / "audio" / "render.mp3").write_bytes(MP3)
    doc, err = vpt._document_from_song_ref(str(outputs / "audio" / "render.mp3"))
    assert err is None and doc.path == "Audio/render.mp3" and _served(doc)
    assert (uploads / "Audio" / "render.mp3").read_bytes() == MP3
    again, _ = vpt._document_from_song_ref(str(outputs / "audio" / "render.mp3"))
    assert again.id == doc.id
    assert sorted(p.name for p in (uploads / "Audio").iterdir()) == ["render.mp3"]

    # A different recording under the same name is kept, not overwritten.
    (outputs / "other").mkdir()
    (outputs / "other" / "render.mp3").write_bytes(MP3 + b"\xff\xfb\x90\x00")
    other, _ = vpt._document_from_song_ref(str(outputs / "other" / "render.mp3"))
    assert other.id != doc.id and other.path != doc.path and _served(other)
    assert (uploads / "Audio" / "render.mp3").read_bytes() == MP3


def test_a_row_with_an_absolute_path_is_moved_onto_the_relative_one(library):
    uploads, _ = library
    (uploads / "old.mp3").write_bytes(MP3)
    legacy = Document(filename="old.mp3", path=str(uploads / "old.mp3"), type="mp3", index_status="STORED")
    db.session.add(legacy)
    db.session.commit()
    assert not _served(legacy)
    doc, err = vpt._document_from_song_ref(str(uploads / "old.mp3"))
    assert err is None and doc.id == legacy.id and doc.path == "old.mp3" and _served(doc)


@pytest.mark.parametrize("name,data", [("notes.mp3", b"not audio at all"), ("notes.txt", b"hello")])
def test_only_audio_files_are_taken_as_songs(library, name, data):
    uploads, _ = library
    (uploads / name).write_bytes(data)
    doc, err = vpt._document_from_song_ref(str(uploads / name))
    assert doc is None and "not an audio file" in err
    assert Document.query.count() == 0


def test_a_voice_reference_clip_is_not_a_song(library):
    uploads, _ = library
    (uploads / "voice_references").mkdir()
    (uploads / "voice_references" / "me.wav").write_bytes(WAV)
    doc, err = vpt._document_from_song_ref(str(uploads / "voice_references" / "me.wav"))
    assert doc is None and "voice reference clip" in err
    assert Document.query.count() == 0
