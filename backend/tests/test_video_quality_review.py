"""VLM video quality reviewer — parse, and the "not reviewed" state (no model/ffmpeg).

The installed-model lookup is stubbed at _installed_ollama_tags, the seam
review_video_quality reads before it samples a frame; ollama.chat is stubbed
through sys.modules. Nothing here reaches Ollama.
"""
import json
import os
import sys
import types
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services import video_consistency_metrics as vcm  # noqa: E402

MODEL = vcm.DEFAULT_VIDEO_REVIEW_MODEL


@pytest.fixture(autouse=True)
def _review_model_installed(monkeypatch):
    monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: {MODEL, "gemma4:e2b"})
    monkeypatch.delenv("GUAARDVARK_VIDEO_REVIEW_MODEL", raising=False)


def _fake_ollama(content):
    mod = types.SimpleNamespace()
    mod.chat = lambda **kw: {"message": {"content": content}}
    return mod


class TestReviewVideoQuality:
    def test_no_frames_is_not_reviewed(self):
        with patch.object(vcm, "_extract_frames_b64", return_value=[]):
            r = vcm.review_video_quality("nope.mp4")
        assert r["available"] is False
        assert r["status"] == "not_reviewed"
        assert r["reason"] == "no_frames"

    def test_valid_review_parsed_and_score_clamped(self):
        payload = (
            '{"summary": "a cat walks", "temporal_coherence": "stable", '
            '"artifacts": [], "quality_score": 42, "justification": "clean"}'
        )
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64a", "b64b"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 2.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama(payload)}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is True
        assert r["status"] == "reviewed"
        assert r["model"] == MODEL
        assert r["frames_reviewed"] == 2
        assert r["review"]["quality_score"] == 10  # clamped from 42
        assert r["review"]["summary"] == "a cat walks"

    def test_score_is_unchanged_when_the_model_is_installed(self):
        payload = '{"summary": "x", "artifacts": ["blur"], "quality_score": 4}'
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama(payload)}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["status"] == "reviewed" and r["review"]["quality_score"] == 4

    def test_vlm_exception_is_not_reviewed(self):
        boom = types.SimpleNamespace()

        def _raise(**kw):
            raise RuntimeError("ollama down")
        boom.chat = _raise
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": boom}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is False
        assert r["status"] == "not_reviewed"
        assert r["reason"] == "vlm_unavailable"
        assert "ollama down" in r["message"]

    def test_unparseable_review_reported(self):
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama("not json at all")}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is False
        assert r["status"] == "not_reviewed"
        assert r["reason"] == "unparseable_review"

    def test_a_json_reply_that_is_not_an_object_is_unparseable(self):
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama("[7]")}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["status"] == "not_reviewed" and r["reason"] == "unparseable_review"

    def test_non_numeric_score_is_not_reviewed(self):
        payload = '{"summary": "x", "quality_score": "great", "artifacts": []}'
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama(payload)}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is True
        assert r["review"]["quality_score"] is None
        assert r["status"] == "not_reviewed" and r["reason"] == "no_score"


class TestReviewModelResolution:
    def test_an_uninstalled_model_is_known_before_any_frame_is_read(self, monkeypatch):
        monkeypatch.setenv("GUAARDVARK_VIDEO_REVIEW_MODEL", "not-pulled-vlm:7b")
        with patch.object(vcm, "_extract_frames_b64") as frames:
            r = vcm.review_video_quality("clip.mp4")
        frames.assert_not_called()
        assert r["status"] == "not_reviewed"
        assert r["reason"] == "model_not_installed"
        assert r["model"] == "not-pulled-vlm:7b"
        assert "not-pulled-vlm:7b is not installed" in r["message"]

    def test_no_other_vision_model_is_substituted(self, monkeypatch):
        monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: {"llava:13b", "qwen2.5vl:7b"})
        assert vcm.resolve_review_model(MODEL) == (None, "model_not_installed")

    def test_ollama_unreachable_is_its_own_reason(self, monkeypatch):
        monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: None)
        with patch.object(vcm, "_extract_frames_b64") as frames:
            r = vcm.review_video_quality("clip.mp4")
        frames.assert_not_called()
        assert r["reason"] == "ollama_unreachable"

    def test_a_bare_name_matches_its_latest_tag(self, monkeypatch):
        monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: {"minicpm-v4.5:latest"})
        assert vcm.resolve_review_model("minicpm-v4.5") == ("minicpm-v4.5:latest", None)

    def test_annotate_writes_the_not_reviewed_record(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vcm, "_installed_ollama_tags", lambda: set())
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"x")
        vcm.review_video_quality(clip, annotate=True)
        side = json.loads((tmp_path / "clip.mp4.metrics.json").read_text())
        assert side["vlm_review"]["status"] == "not_reviewed"
