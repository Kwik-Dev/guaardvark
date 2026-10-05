"""The drawtext capability probe (HANDOFF-cli-coverage-2026-10-05 §0).

`drawtext` is an optional ffmpeg filter. Homebrew's slim `ffmpeg` formula has no
font libraries, so on such a box the text render died inside a worker with
`ffmpeg exit 8: Filter not found` — a message that names neither the cause nor
the fix. These tests pin the probe that replaces it, on both the positive and
the negative path. The machine's own ffmpeg is not consulted: the listing is
mocked, so this passes on a build without drawtext and on one with it.
"""
from __future__ import annotations

import subprocess

import pytest

from backend.services import video_text_overlay as vto
from backend.services import video_timeline_render as vtr


class _Listing:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


_DRAWTEXT_LINE = " ... drawtext          V->V       Draw text on top of video.\n"
_FILTERS_WITHOUT_DRAWTEXT = """
Filters:
  ... drawbox           V->V       Draw a colored box on the input video.
  ... drawgrid          V->V       Draw a grid on the input video.
  ... drawbox           V->V       Draw a box; unlike drawtext it needs no font.
"""


def _fake_run(stdout: str, returncode: int = 0):
    def run(*_args, **_kwargs):
        return _Listing(stdout, returncode)
    return run


def test_probe_finds_drawtext(monkeypatch):
    monkeypatch.setattr(
        vto.subprocess, "run", _fake_run("Filters:\n" + _DRAWTEXT_LINE)
    )
    assert vto._ffmpeg_has_drawtext() is True


def test_probe_reports_a_build_without_it(monkeypatch):
    # 'drawbox' and 'drawgrid' are present but must NOT count as drawtext, and a
    # *description* that merely names drawtext (the third line) must not either.
    monkeypatch.setattr(vto.subprocess, "run", _fake_run(_FILTERS_WITHOUT_DRAWTEXT))
    assert vto._ffmpeg_has_drawtext() is False


def test_probe_is_unqueryable_when_ffmpeg_cannot_be_started(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("No such file or directory: 'ffmpeg'")

    monkeypatch.setattr(vto.subprocess, "run", boom)
    assert vto._ffmpeg_has_drawtext() is None


def test_probe_is_unqueryable_on_a_nonzero_exit(monkeypatch):
    monkeypatch.setattr(vto.subprocess, "run", _fake_run(_DRAWTEXT_LINE, returncode=1))
    assert vto._ffmpeg_has_drawtext() is None


def test_require_drawtext_names_the_cause_and_the_fix(monkeypatch):
    monkeypatch.setattr(vto.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: False)

    with pytest.raises(vto.VideoOverlayError) as excinfo:
        vto.require_drawtext()

    message = str(excinfo.value)
    assert "drawtext" in message
    assert "libfreetype" in message
    assert "ffmpeg-full" in message          # the macOS fix
    assert "--engine editor" in message      # the MLT fallback


def test_require_drawtext_distinguishes_an_unqueryable_ffmpeg(monkeypatch):
    """An ffmpeg that runs but cannot be queried is not 'a build without drawtext'."""
    monkeypatch.setattr(vto.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: None)

    with pytest.raises(vto.VideoOverlayError) as excinfo:
        vto.require_drawtext()

    message = str(excinfo.value)
    assert "Could not run" in message
    assert "libfreetype" not in message  # not a build-capability claim


def test_require_drawtext_reports_a_missing_binary(monkeypatch):
    monkeypatch.setattr(vto.shutil, "which", lambda _name: None)

    with pytest.raises(vto.VideoOverlayError) as excinfo:
        vto.require_drawtext()

    message = str(excinfo.value)
    assert "PATH" in message
    assert "libfreetype" not in message  # no point advising a build fix


def test_require_drawtext_passes_on_a_capable_build(monkeypatch):
    monkeypatch.setattr(vto.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: True)
    vto.require_drawtext()  # must not raise


def test_overlay_refuses_legibly_before_touching_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: False)
    monkeypatch.setattr(vto.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    # The input does not exist — but the capability error must win, because it
    # is the actionable one.
    with pytest.raises(vto.VideoOverlayError) as excinfo:
        vto.add_text_to_video(
            input_path=tmp_path / "missing.mp4",
            output_path=tmp_path / "out.mp4",
            text="hello",
        )
    assert "libfreetype" in str(excinfo.value)


def _timeline_payload():
    return {
        "text_elements": [
            {"text": "caption", "position": "bottom-center", "startSeconds": 0, "endSeconds": 1}
        ]
    }


def test_timeline_refuses_legibly_when_text_needs_drawtext(monkeypatch, tmp_path):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"\x00")
    # shutil is one module object, so patching it here also reaches vto's call to
    # require_drawtext: the real imported function is exercised, not a stub.
    monkeypatch.setattr(vtr.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: False)
    monkeypatch.setattr(vtr, "resolve_font_path", lambda: "/tmp/Fake-Bold.ttf")

    with pytest.raises(vto.VideoOverlayError) as excinfo:
        vtr.render_timeline(
            video_input_path=video,
            output_path=tmp_path / "out.mp4",
            text_elements=_timeline_payload()["text_elements"],
        )
    assert "libfreetype" in str(excinfo.value)


def test_timeline_without_text_does_not_require_drawtext(monkeypatch, tmp_path):
    """A trim/passthrough render must keep working on a build without drawtext."""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setattr(vtr.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(vto, "_ffmpeg_has_drawtext", lambda: False)

    called = {}

    def fake_subprocess_run(cmd, **kwargs):
        called["cmd"] = cmd
        # render_timeline verifies the output exists and is non-empty; the real
        # ffmpeg would write it, so the stub does too.
        open(cmd[-1], "wb").write(b"\x00")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(vtr.subprocess, "run", fake_subprocess_run)

    out = tmp_path / "out.mp4"
    vtr.render_timeline(video_input_path=video, output_path=out, text_elements=[])

    assert "drawtext" not in " ".join(called["cmd"])
    assert out.exists()
