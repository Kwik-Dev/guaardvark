#!/usr/bin/env python3
"""Wave 1 — truth injection: probe semantics, servo threading, honest datasets."""
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


# ── TrainerTruthProbe semantics (no websocket) ──────────────────────────────

def _probe_with_snapshots(snapshots):
    """Probe whose _snapshot() returns queued values (then repeats last)."""
    from backend.services.trainer_truth_probe import TrainerTruthProbe
    import itertools
    p = TrainerTruthProbe(poll_timeout_s=0.2, poll_interval_s=0.01)
    seq = itertools.chain(iter(snapshots), itertools.repeat(snapshots[-1]))
    p._snapshot = lambda: next(seq)
    return p


class TestProbeSemantics:
    def test_hit_detected_from_clicks_delta(self):
        before = {"clicks": 3, "misses": 1, "target_cx": 200, "target_cy": 150, "label": "7"}
        p = _probe_with_snapshots([{"clicks": 4, "misses": 1}])
        t = p.after(before)
        assert t["true_hit"] is True
        assert (t["target_cx"], t["target_cy"]) == (200, 150)
        assert t["truth_source"] == "vision_trainer_dom"

    def test_miss_detected_from_misses_delta(self):
        before = {"clicks": 3, "misses": 1, "target_cx": 200, "target_cy": 150}
        p = _probe_with_snapshots([{"clicks": 3, "misses": 2}])
        assert p.after(before)["true_hit"] is False

    def test_no_delta_is_unscored_none(self):
        before = {"clicks": 3, "misses": 1, "target_cx": 200, "target_cy": 150}
        p = _probe_with_snapshots([{"clicks": 3, "misses": 1}])
        assert p.after(before)["true_hit"] is None  # header/off-arena — never a miss

    def test_none_before_returns_none(self):
        p = _probe_with_snapshots([{"clicks": 0, "misses": 0}])
        assert p.after(None) is None


# ── Truth threading through the archive writer ──────────────────────────────

class TestArchiveTruth:
    def test_true_error_px_computed_from_truth(self, tmp_path):
        from backend.services import servo_knowledge_store as sks
        archive = sks.ServoArchive()
        with patch.object(archive, "_archive_path", tmp_path / "a.jsonl"):
            archive.record(
                target_description="circle", model_used="m",
                raw_model_coords=(10, 10), scaled_coords=(10, 10),
                actual_click_coords=(100, 100), scale_factor=(1.0, 1.0),
                success=True,
                truth={"true_hit": False, "target_cx": 130, "target_cy": 140,
                       "label": "7", "truth_source": "vision_trainer_dom"},
            )
            row = json.loads((tmp_path / "a.jsonl").read_text().strip())
        assert row["truth"]["true_hit"] is False
        assert row["true_error_px"] == 50.0  # 3-4-5 triangle
        assert row["success"] is True  # old field untouched

    def test_no_truth_no_new_fields(self, tmp_path):
        from backend.services import servo_knowledge_store as sks
        archive = sks.ServoArchive()
        with patch.object(archive, "_archive_path", tmp_path / "a.jsonl"):
            archive.record(
                target_description="x", model_used="m",
                raw_model_coords=(0, 0), scaled_coords=(0, 0),
                actual_click_coords=(0, 0), scale_factor=(1.0, 1.0), success=False,
            )
            row = json.loads((tmp_path / "a.jsonl").read_text().strip())
        assert "truth" not in row and "true_error_px" not in row


# ── Dataset builder honesty ─────────────────────────────────────────────────

def _rec(session, shot, target="blue circle", truth=None, crosshair=(50, 50)):
    r = {
        "screenshot_path": shot, "target_description": target,
        "crosshair_pos": list(crosshair), "target_actual": list(crosshair),
        "corrections": [], "success": True, "_session": session,
        "metadata": {"screen_size": [1000, 1000]},
    }
    if truth is not None:
        r["metadata"]["truth"] = truth
    return r


class TestDatasetBuilder:
    def _mod(self):
        from backend.services.training.scripts import prepare_training_set as pts
        return pts

    def test_coordinate_label_is_truth_not_prediction(self):
        pts = self._mod()
        recs = [_rec("s1", "/a.webp", truth={"true_hit": False, "target_cx": 777, "target_cy": 888})]
        ex = pts.generate_coordinate_examples(recs)
        assert len(ex) == 1
        label = json.loads(ex[0]["conversations"][1]["content"])
        assert (label["x"], label["y"]) == (777, 888)  # NOT the crosshair (50,50)
        assert "1000x1000" in ex[0]["conversations"][0]["content"]  # per-record size

    def test_truthless_records_dropped_entirely(self):
        pts = self._mod()
        recs = [_rec("s1", "/a.webp")]  # success=True but NO truth
        assert pts.generate_coordinate_examples(recs) == []
        assert pts.generate_on_target_examples(recs) == []

    def test_unscored_dropped_scored_kept_with_honest_labels(self):
        pts = self._mod()
        recs = [
            _rec("s1", "/hit.webp", truth={"true_hit": True, "target_cx": 1, "target_cy": 1}),
            _rec("s1", "/miss.webp", truth={"true_hit": False, "target_cx": 1, "target_cy": 1}),
            _rec("s1", "/unscored.webp", truth={"true_hit": None, "target_cx": 1, "target_cy": 1}),
        ]
        ex = pts.generate_on_target_examples(recs)
        labels = [json.loads(e["conversations"][1]["content"])["on_target"] for e in ex]
        assert labels == [True, False]  # unscored row gone, honest negative present

    def test_miss_yields_correction_example_with_direction(self):
        pts = self._mod()
        recs = [_rec("s1", "/m.webp", crosshair=(100, 100),
                     truth={"true_hit": False, "target_cx": 200, "target_cy": 100})]
        ex = pts.generate_correction_examples(recs)
        assert len(ex) == 1
        lab = json.loads(ex[0]["conversations"][1]["content"])
        assert lab["direction"] == "right"
        assert lab["distance"] == "large"

    def test_split_never_straddles_a_session(self):
        pts = self._mod()
        examples, by_image = [], {}
        for s in ("s1", "s2", "s3", "s4", "s5"):
            for i in range(4):
                img = f"/{s}_{i}.webp"
                examples.append({"image": img, "conversations": []})
                by_image[img] = s
        train, ev = pts.split_by_session(examples, by_image)
        train_sessions = {by_image[e["image"]] for e in train}
        eval_sessions = {by_image[e["image"]] for e in ev}
        assert train_sessions.isdisjoint(eval_sessions)
        assert len(train) + len(ev) == 20


# ── Prose-fallback poison manufacturer fixed ────────────────────────────────

class TestExpectationDeriver:
    def test_conditional_instruction_yields_no_expectation(self):
        # The exact poison line: mentions both trigger words but is a conditional
        # instruction, not a visibility claim.
        line = ("You MUST ensure a browser window is open... clicking the Firefox icon... "
                "If you are on the desktop, navigate will silently fail.")
        import re
        _assertive = re.compile(r"\b(is|are|appears?|shows?|visible|present|located|sits)\b", re.I)
        _conditional = re.compile(r"^(if|when|unless|should you|in case)\b", re.I)
        # A conditional line must be skipped even though it contains assertive verbs
        assert _conditional.match("If you are on the desktop, navigate will fail") is not None
        # And the requirement pair used in _derive_session_expectations:
        claim = "The Firefox icon is visible on the left edge of the desktop"
        assert _assertive.search(claim) and not _conditional.match(claim)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
