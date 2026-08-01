"""VLM video quality reviewer — parse + fail-open behavior (no model/ffmpeg)."""
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services import video_consistency_metrics as vcm  # noqa: E402


def _fake_ollama(content):
    mod = types.SimpleNamespace()
    mod.chat = lambda **kw: {"message": {"content": content}}
    return mod


class TestReviewVideoQuality:
    def test_no_frames_fails_open(self):
        with patch.object(vcm, "_extract_frames_b64", return_value=[]):
            r = vcm.review_video_quality("nope.mp4")
        assert r["available"] is False
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
        assert r["frames_reviewed"] == 2
        assert r["review"]["quality_score"] == 10  # clamped from 42
        assert r["review"]["summary"] == "a cat walks"

    def test_vlm_exception_fails_open(self):
        boom = types.SimpleNamespace()
        def _raise(**kw):
            raise RuntimeError("ollama down")
        boom.chat = _raise
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": boom}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is False
        assert "vlm_unavailable" in r["reason"]

    def test_unparseable_review_reported(self):
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama("not json at all")}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is False
        assert r["reason"] == "unparseable_review"

    def test_non_numeric_score_becomes_none(self):
        payload = '{"summary": "x", "quality_score": "great", "artifacts": []}'
        with patch.object(vcm, "_extract_frames_b64", return_value=["b64"]), \
             patch.object(vcm, "compute_basic_video_stats", return_value={"duration_s": 1.0}), \
             patch.dict("sys.modules", {"ollama": _fake_ollama(payload)}):
            r = vcm.review_video_quality("clip.mp4")
        assert r["available"] is True
        assert r["review"]["quality_score"] is None
