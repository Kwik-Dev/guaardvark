"""Burning an SRT onto a video (video-editor captions-burn / captions-status).

The fake routes below return the shapes the BACKEND actually returns, taken from the code
that writes them rather than from this module's reading of them — an earlier round of these
CLI tests invented a payload key and shipped a column that was blank against every real
project, so the shapes are cited here:

* POST /api/video-editor/captions/import answers a BARE {"captions": [{text, start, end}]},
  no success/data envelope (backend/api/video_editor_api.py import_captions).
* POST /api/video-overlay/render-timeline answers 202 through success_response, i.e.
  {"success": true, "status_code": 202, "message": ..., "data": {job_id, status}}
  (backend/api/video_overlay_api.py render_timeline_endpoint).
* GET /api/video-overlay/render-status/<job> answers through success_response too, with
  data {job_id, status, progress, message, document_id}.
* An SRT the backend cannot parse is NOT an empty list: it answers 400 with
  {"error": "No captions parsed from the SRT file"} (video_editor_api.py import_captions).

On the human branch: CliRunner's stdout is not a tty, so output.is_pipe() is true and
print_success / print_kv take their own JSON branch even when --json was not passed. The two
`*_print_path_runs` tests therefore do NOT exercise Rich formatting -- what they do exercise
is that the call is evaluated at all, which is what catches a missing `from llx import
output` (that class of bug has shipped twice here, invisible to every --json test). Whether
the captions actually land in the picture is checked at the request, and the filter itself
in backend/tests/tasks/test_video_render_tasks_import.py.
"""
from __future__ import annotations

import json

import pytest

from llx.main import app


def _run(cli_runner, args, *, expect: int = 0):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == expect, result.output
    return result


def _streams(result):
    """stdout plus stderr. print_warning writes to stderr, so result.output alone misses it."""
    return (result.output or "") + (getattr(result, "stderr", "") or "")


# The bare shape `import_captions` returns.
_IMPORTED = {"captions": [
    {"text": "First line.", "start": 0.0, "end": 1.5},
    {"text": "Second line.", "start": 1.5, "end": 3.25},
]}

_IMPORT_PATH = "/api/video-editor/captions/import"
_RENDER_PATH = "/api/video-overlay/render-timeline"
_STATUS_PATH = "/api/video-overlay/render-status/job-1"


def _render_reply(job_id: str = "job-1"):
    """The 202 body `render-timeline` sends, via success_response."""
    return {
        "success": True,
        "timestamp": "2026-10-05T00:00:00",
        "status_code": 202,
        "message": "Render dispatched",
        "data": {"job_id": job_id, "status": "pending"},
    }


def _status_reply(**over):
    """The body `render-status` sends, via success_response."""
    payload = {
        "job_id": "job-1",
        "status": "running",
        "progress": 40.0,
        "message": "Burning captions",
        "document_id": None,
    }
    payload.update(over)
    return {
        "success": True,
        "timestamp": "2026-10-05T00:00:00",
        "status_code": 200,
        "message": "Operation completed successfully",
        "data": payload,
    }


# --- captions-burn: the request it builds ----------------------------------

def test_srt_is_parsed_by_the_backend_then_burned(fake_backend, cli_runner, isolated_home):
    """The SRT goes through the backend's own parser, and every cue becomes one element."""
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    payload = json.loads(
        _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt", "--json"]).output
    )

    assert fake_backend.posted_paths() == [_IMPORT_PATH, _RENDER_PATH]

    assert json.loads(fake_backend.calls[0][2]) == {"path": "/tmp/cues.srt"}

    body = json.loads(fake_backend.calls[1][2])
    assert body["video_document_id"] == 42
    assert body["text_elements"] == [
        {"text": "First line.", "startSeconds": 0.0, "endSeconds": 1.5},
        {"text": "Second line.", "startSeconds": 1.5, "endSeconds": 3.25},
    ]
    assert payload["data"] == {"job_id": "job-1", "status": "pending"}


def test_captions_doc_is_sent_as_document_id_not_path(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--captions-doc", "7", "--json"])

    assert json.loads(fake_backend.calls[0][2]) == {"document_id": 7}


def test_style_options_are_sent_only_when_given(fake_backend, cli_runner, isolated_home):
    """No explicit nulls: the backend has its own defaults and `null` would replace them."""
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt", "--json"])
    bare = json.loads(fake_backend.calls[-1][2])

    assert all("fontSize" not in e and "fontColor" not in e for e in bare["text_elements"])
    assert "audio_document_id" not in bare and "backend" not in bare

    _run(cli_runner, [
        "video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
        "--font-size", "36", "--color", "#ffcc00", "--audio", "9", "--backend", "mlt", "--json",
    ])
    styled = json.loads(fake_backend.calls[-1][2])

    assert all(e["fontSize"] == 36 and e["fontColor"] == "#ffcc00" for e in styled["text_elements"])
    assert styled["audio_document_id"] == 9
    assert styled["backend"] == "mlt"


def test_the_element_keys_are_the_ones_the_route_accepts(fake_backend, cli_runner, isolated_home):
    """The route takes exactly text, fontSize, fontColor, x, y, rotation, startSeconds, endSeconds."""
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                      "--font-size", "36", "--color", "#fff", "--json"])

    allowed = {"text", "fontSize", "fontColor", "position", "x", "y", "rotation",
               "startSeconds", "endSeconds"}
    for element in json.loads(fake_backend.calls[-1][2])["text_elements"]:
        assert set(element) <= allowed, f"invented key(s): {set(element) - allowed}"


def test_an_srt_the_backend_cannot_parse_is_refused(fake_backend, cli_runner, isolated_home):
    """The backend answers 400, not an empty caption list.

    An earlier version of this test faked a bare {"captions": []}, which the route never
    returns, so it asserted a state that cannot occur and left the CLI's own guard looking
    load-bearing when client.post had already raised on the 400.
    """
    fake_backend.route("POST", _IMPORT_PATH, status=400,
                       json={"error": "No captions parsed from the SRT file"})

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/empty.srt", "--json"],
                  expect=1)

    assert "No captions parsed from the SRT file" in result.output
    assert fake_backend.posted_paths() == [_IMPORT_PATH], "must not dispatch a render"


# --- captions-burn: the input contract ------------------------------------

def test_neither_srt_nor_captions_doc_is_refused_before_any_call(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--json"], expect=2)

    assert fake_backend.calls == [], "a usage error must not reach the backend"
    assert "you gave neither" in result.output
    # The group's own shape (video_editor.py:242,281,322): a script must be able to tell a
    # usage error from a server refusal. captions-import, which hits this same route, does.
    assert "MISSING_INPUT" in result.output


def test_both_srt_and_captions_doc_is_refused_before_any_call(fake_backend, cli_runner, isolated_home):
    fake_backend.default(json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42",
                               "--srt", "/tmp/cues.srt", "--captions-doc", "7", "--json"], expect=2)

    assert fake_backend.calls == []
    assert "you gave both" in result.output


def test_a_position_and_raw_pixels_together_are_refused(fake_backend, cli_runner, isolated_home):
    """The renderer lets position win, so the pixels would vanish without a word."""
    fake_backend.default(json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                               "--position", "bottom-center", "--y", "900", "--json"], expect=2)

    assert fake_backend.calls == []
    assert "not both" in result.output


def test_a_font_size_below_one_is_refused(fake_backend, cli_runner, isolated_home):
    """It would otherwise fail inside ffmpeg, in the queue, after this reported success."""
    fake_backend.default(json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                               "--font-size", "0", "--json"], expect=2)

    assert fake_backend.calls == []
    assert "at least 1" in result.output


# --- placement, and cues that can never show -------------------------------

def test_position_is_sent_as_a_name_and_never_as_pixels(fake_backend, cli_runner, isolated_home):
    """The CLI cannot know the frame size, so it sends the name and lets drawtext place it."""
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                      "--position", "bottom-center", "--json"])

    elements = json.loads(fake_backend.calls[-1][2])["text_elements"]
    assert all(e["position"] == "bottom-center" for e in elements)
    assert all("x" not in e and "y" not in e for e in elements), (
        "pixels sent alongside a position would be silently ignored by the renderer"
    )


@pytest.mark.parametrize("position", [
    "top-left", "top-center", "top-right", "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
])
def test_every_advertised_position_is_accepted(fake_backend, cli_runner, isolated_home, position):
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                      "--position", position, "--json"])

    assert json.loads(fake_backend.calls[-1][2])["text_elements"][0]["position"] == position


def test_an_unknown_position_is_rejected_by_the_option_parser(fake_backend, cli_runner, isolated_home):
    """A typo must not silently fall back to the backend's mid-picture pixel default."""
    fake_backend.default(json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                               "--position", "sideways", "--json"], expect=2)

    assert fake_backend.calls == []


def test_raw_x_and_y_are_passed_through_for_any_backend(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt",
                      "--x", "120", "--y", "980", "--json"])

    elements = json.loads(fake_backend.calls[-1][2])["text_elements"]
    assert all(e["x"] == 120 and e["y"] == 980 for e in elements)


def test_a_cue_that_ends_before_it_starts_is_warned_about(fake_backend, cli_runner, isolated_home):
    """enable='between(t,5.0,3.0)' matches no frame, so the caption silently never shows.

    It fails in a Celery worker long after this command reported success, so the warning has
    to happen here. print_warning writes to stderr, so the assertion reads both streams.
    """
    fake_backend.route("POST", _IMPORT_PATH, json={"captions": [
        {"text": "fine", "start": 0.0, "end": 1.0},
        {"text": "backwards", "start": 5.0, "end": 3.0},
    ]})
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt"])

    assert "1 cue(s) end at or before they start" in _streams(result)
    assert fake_backend.posted_paths() == [_IMPORT_PATH, _RENDER_PATH], "the good cue still renders"


# --- captions-status -------------------------------------------------------

def test_status_reads_the_render_status_route(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", _STATUS_PATH, json=_status_reply(document_id=99, status="completed"))

    payload = json.loads(_run(cli_runner, ["video-editor", "captions-status", "job-1", "--json"]).output)

    assert payload["data"]["document_id"] == 99
    assert payload["data"]["status"] == "completed"
    assert len(fake_backend.calls_for("GET", _STATUS_PATH)) == 1


# --- the branch `--json` does not reach ------------------------------------

def test_burn_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", _IMPORT_PATH, json=_IMPORTED)
    fake_backend.route("POST", _RENDER_PATH, status=202, json=_render_reply())

    result = _run(cli_runner, ["video-editor", "captions-burn", "42", "--srt", "/tmp/cues.srt"])

    assert "job-1" in result.output


def test_status_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", _STATUS_PATH, json=_status_reply(status="completed", document_id=99))

    result = _run(cli_runner, ["video-editor", "captions-status", "job-1"])

    assert "completed" in result.output
    assert "99" in result.output
