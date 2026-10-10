"""Start images on /batch-video/generate/image: a file path passes as given,
a library document id resolves to its file, and a missing one refuses the
batch before anything is queued.

No backend, database, GPU or ComfyUI: ``_media_path`` is replaced by a table.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.api import batch_video_generation_api as api


def _table(entries):
    def media_path(ref, label):
        if ref in entries:
            return entries[ref], None
        return None, f"{label} document {ref} was not found, or its file is missing."
    return media_path


def test_a_file_path_passes_as_given(tmp_path, monkeypatch):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    monkeypatch.setattr(api, "_media_path", _table({}))

    frames, err = api._start_frames([str(frame)])

    assert err is None
    assert frames == [str(frame)]


def test_a_document_id_resolves_like_the_end_frame(tmp_path, monkeypatch):
    uploaded = tmp_path / "Images" / "start.png"
    monkeypatch.setattr(api, "_media_path", _table({"42": str(uploaded)}))

    frames, err = api._start_frames(["42"])

    assert err is None
    assert frames == [str(uploaded)]


def test_a_missing_reference_refuses_the_batch(monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({}))

    frames, err = api._start_frames(["42"])

    assert frames is None
    assert "Start image 1" in err and "42" in err


def test_an_empty_reference_refuses_the_batch(monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({}))

    frames, err = api._start_frames(["", "7"])

    assert frames is None
    assert err == "Start image 1 is empty."
