"""Start images on /batch-video/generate/image.

A library document id, or a path inside the install, resolves through the
same ``_media_path`` the end frame uses, for any caller. A path to a file
anywhere else is honoured only for a caller on this machine (the CLI and
the video skill), never a credential file, and a frame has to be an image.
A missing reference refuses the batch before anything is queued.

No backend, database, GPU or ComfyUI: ``_media_path`` is replaced by a
table and the caller check by a flag.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.api import batch_video_generation_api as api


def _table(entries):
    """``_media_path`` as the resolver would answer: a known document id or
    install path resolves; anything else is refused."""
    def media_path(ref, label):
        if ref in entries:
            return entries[ref], None
        return None, f"{label} '{ref}' was refused: it must be inside the uploads or outputs directory or the install root."
    return media_path


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"png")
    return path


def test_a_document_id_resolves_like_the_end_frame_for_any_caller(tmp_path, monkeypatch):
    uploaded = tmp_path / "Images" / "start.png"
    monkeypatch.setattr(api, "_media_path", _table({"42": str(uploaded)}))
    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: False)

    assert api._start_frames(["42"]) == ([str(uploaded)], None)


def test_a_path_inside_the_install_resolves_for_any_caller(frame, monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({str(frame): str(frame)}))
    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: False)

    assert api._start_frames([str(frame)]) == ([str(frame)], None)


def test_a_file_elsewhere_passes_only_for_a_caller_on_this_machine(frame, monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({}))

    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: True)
    assert api._start_frames([str(frame)]) == ([str(frame)], None)

    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: False)
    frames, err = api._start_frames([str(frame)])
    assert frames is None
    assert "refused" in err and "Start image 1" in err


def test_a_credential_file_is_refused_even_on_this_machine(tmp_path, monkeypatch):
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"not a picture")
    monkeypatch.setattr(api, "_media_path", _table({}))
    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: True)

    frames, err = api._start_frames([str(secret)])

    assert frames is None
    assert "refused" in err


def test_a_frame_has_to_be_an_image(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    monkeypatch.setattr(api, "_media_path", _table({"7": str(clip)}))
    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: False)

    frames, err = api._start_frames(["7"])

    assert frames is None
    assert err == "Start image 1 is not an image: clip.mp4."


def test_a_missing_reference_refuses_the_batch(monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({}))
    monkeypatch.setattr(api, "_caller_is_this_machine", lambda: True)

    frames, err = api._start_frames(["42"])

    assert frames is None
    assert "Start image 1" in err and "42" in err


def test_an_empty_reference_refuses_the_batch(monkeypatch):
    monkeypatch.setattr(api, "_media_path", _table({}))

    frames, err = api._start_frames(["", "7"])

    assert frames is None
    assert err == "Start image 1 is empty."
