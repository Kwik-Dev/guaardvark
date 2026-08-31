"""Tests for video_pipeline NVENC size limits, codec pick, and encode fallback."""
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from service import video_pipeline as vp


@pytest.fixture(autouse=True)
def _reset_nvenc_cache():
    vp._nvenc_works = None
    vp._encoder_size_ok.clear()
    yield
    vp._nvenc_works = None
    vp._encoder_size_ok.clear()


def test_even_dims():
    assert vp._even(1920) == 1920
    assert vp._even(1921) == 1922
    assert vp._even(1) == 2
    assert vp._even(0) == 2


def test_pick_codecs_nvenc_and_software():
    assert vp._pick_codecs(True, False) == ("h264_nvenc", "yuv420p", "libx264")
    assert vp._pick_codecs(True, True) == ("hevc_nvenc", "yuv420p10le", "libx265")
    assert vp._pick_codecs(False, False) == ("libx264", "yuv420p", "libx264")
    assert vp._pick_codecs(False, True) == ("libx265", "yuv420p10le", "libx265")


def test_pick_codecs_for_size_2880_keeps_h264():
    with patch.object(vp, "_check_nvenc_available", return_value=True):
        with patch.object(vp, "_probe_encoder_at_size", side_effect=lambda c, w, h: (True, "")):
            vcodec, pix, soft, reason = vp._pick_codecs_for_size(2880, 2880, False)
    assert vcodec == "h264_nvenc"
    assert pix == "yuv420p"
    assert soft == "libx264"
    assert reason == "h264_ok"


def test_pick_codecs_for_size_4320_uses_hevc():
    """1080*4=4320 exceeds h264_nvenc 4096 — must pick hevc_nvenc when probe OK."""
    def probe(codec, w, h):
        if codec == "h264_nvenc":
            return False, "Width 4320 exceeds 4096"
        if codec == "hevc_nvenc":
            return True, ""
        return False, "no"

    with patch.object(vp, "_check_nvenc_available", return_value=True):
        with patch.object(vp, "_encoder_listed", return_value=True):
            with patch.object(vp, "_probe_encoder_at_size", side_effect=probe):
                vcodec, pix, soft, reason = vp._pick_codecs_for_size(4320, 4320, False)
    assert vcodec == "hevc_nvenc"
    assert pix == "yuv420p"
    assert soft == "libx264"
    assert "hevc_nvenc" in reason
    assert "oversize" in reason or "h264" in reason


def test_pick_codecs_for_size_4320_software_when_no_hevc():
    with patch.object(vp, "_check_nvenc_available", return_value=True):
        with patch.object(vp, "_encoder_listed", return_value=False):
            with patch.object(
                vp, "_probe_encoder_at_size", return_value=(False, "exceeds 4096")
            ):
                vcodec, pix, soft, reason = vp._pick_codecs_for_size(4320, 4320, False)
    assert vcodec == "libx264"
    assert reason == "software"


def test_check_nvenc_listed_but_probe_fails():
    with patch.object(vp, "_encoder_listed", return_value=True):
        with patch("service.video_pipeline.subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stderr="No capable devices found", stdout="")
            assert vp._check_nvenc_available() is False
            assert vp._check_nvenc_available() is False
            assert run.call_count == 1


def test_check_nvenc_probe_ok():
    with patch.object(vp, "_encoder_listed", return_value=True):
        with patch("service.video_pipeline.subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            assert vp._check_nvenc_available() is True


def test_get_video_info_structure():
    mock_probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "nb_frames": "300",
                "pix_fmt": "yuv420p",
            },
            {"codec_type": "audio"},
        ]
    }
    with patch("service.video_pipeline.ffmpeg.probe", return_value=mock_probe):
        info = vp.get_video_info("/fake/path.mp4")
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["fps"] == 30.0
        assert info["nb_frames"] == 300
        assert info["has_audio"] is True
        assert info["pix_fmt"] == "yuv420p"


def test_early_broken_pipe_restarts_from_frame_zero(tmp_path):
    """Pipe-buffered writes (frames=2) must still trigger full restart with software."""
    inp = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    inp.write_bytes(b"fake")

    calls = {"n": 0}

    def fake_encode(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise vp.EarlyEncodePipeError(
                vcodec=kwargs["vcodec"],
                frames_written=2,
                out_width=kwargs["out_width"],
                out_height=kwargs["out_height"],
                detail="Width 4320 exceeds 4096",
            )
        # Second pass (software) succeeds and creates tmp
        open(kwargs["tmp_output"], "wb").write(b"fake-mp4")
        return 10

    with patch.object(vp, "get_video_info", return_value={
        "width": 1080, "height": 1080, "fps": 30.0, "nb_frames": 10,
        "has_audio": False, "pix_fmt": "yuv420p",
    }):
        with patch.object(vp, "_check_nvdec_available", return_value=False):
            with patch.object(
                vp, "_pick_codecs_for_size",
                return_value=("h264_nvenc", "yuv420p", "libx264", "h264_ok"),
            ):
                with patch.object(vp, "_encode_pass", side_effect=fake_encode):
                    vp.process_video(
                        str(inp),
                        str(out),
                        frame_processor=lambda f: f,
                        out_width=4320,
                        out_height=4320,
                    )

    assert calls["n"] == 2
    assert out.exists()


def test_process_video_falls_back_on_first_write_broken_pipe(tmp_path):
    """When NVENC writer dies on first frame, retry full encode with libx264."""
    inp = tmp_path / "in.mp4"
    out = tmp_path / "out.mp4"
    inp.write_bytes(b"fake")

    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    raw = frame.tobytes()

    def make_reader():
        reader = MagicMock()
        reader.stdout.read.side_effect = [raw, b""]
        reader.poll.return_value = 0
        return reader

    nvenc_writer = MagicMock()
    nvenc_writer.stdin.write.side_effect = BrokenPipeError("nvenc dead")
    nvenc_writer.poll.return_value = 1

    soft_writer = MagicMock()
    soft_writer.wait.return_value = 0
    soft_writer.poll.return_value = 0

    writers = [nvenc_writer, soft_writer]
    readers = [make_reader(), make_reader()]

    def fake_build(tmp_output, *args, **kwargs):
        w = writers.pop(0)
        if not writers:  # soft writer about to run
            open(tmp_output, "wb").write(b"fake-mp4")
        return w

    with patch.object(vp, "get_video_info", return_value={
        "width": 2, "height": 2, "fps": 24.0, "nb_frames": 1,
        "has_audio": False, "pix_fmt": "yuv420p",
    }):
        with patch.object(vp, "_check_nvdec_available", return_value=False):
            with patch.object(
                vp, "_pick_codecs_for_size",
                return_value=("h264_nvenc", "yuv420p", "libx264", "h264_ok"),
            ):
                with patch.object(vp, "_open_reader", side_effect=lambda *a, **k: readers.pop(0)):
                    with patch.object(vp, "_build_writer", side_effect=fake_build):
                        with patch.object(vp, "_drain_and_kill", return_value="exceeds 4096"):
                            vp.process_video(
                                str(inp),
                                str(out),
                                frame_processor=lambda f: f,
                                out_width=2,
                                out_height=2,
                            )

    soft_writer.stdin.write.assert_called()
    assert out.exists()


def test_pre_process_accepts_nonwritable_frame():
    """Frames from ffmpeg pipes are often non-writable — must copy before torch."""
    from service.upscaler import _pre_process

    img = np.zeros((16, 16, 3), dtype=np.uint8)
    img.setflags(write=False)
    tensor = _pre_process(img, device="cpu", precision="fp32")
    assert tensor.shape == (1, 3, 16, 16)
