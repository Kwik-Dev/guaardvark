"""Tier-1.5 servo calibration — store round-trip, apply math, guards."""
import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services import servo_knowledge_store as sks  # noqa: E402
from backend.tools.servo_calibrate import fit_axis  # noqa: E402


class TestStore:
    def _tmp_store(self, tmp_path):
        return patch.object(sks, "_CALIBRATION_PATH", tmp_path / "servo_calibration.json")

    def test_round_trip(self, tmp_path):
        with self._tmp_store(tmp_path):
            sks._calibration_cache.update(mtime=None, data={})
            fit = {"a_x": -15.7, "b_x": 1.0412, "a_y": 222.9, "b_y": 0.5777, "samples": 12}
            sks.save_servo_calibration("gemma4:e4b", 1000, 1000, fit)
            got = sks.load_servo_calibration("gemma4:e4b", 1000, 1000)
        assert got == {"a_x": -15.7, "b_x": 1.0412, "a_y": 222.9, "b_y": 0.5777}

    def test_missing_returns_none(self, tmp_path):
        with self._tmp_store(tmp_path):
            sks._calibration_cache.update(mtime=None, data={})
            assert sks.load_servo_calibration("gemma4:e4b", 1000, 1000) is None

    def test_resolution_keyed(self, tmp_path):
        with self._tmp_store(tmp_path):
            sks._calibration_cache.update(mtime=None, data={})
            sks.save_servo_calibration("gemma4:e4b", 1000, 1000,
                                       {"a_x": 0, "b_x": 1, "a_y": 0, "b_y": 1})
            # different resolution → no calibration (never apply a square fit to 4:3)
            assert sks.load_servo_calibration("gemma4:e4b", 1280, 960) is None

    def test_degenerate_slope_refused(self, tmp_path):
        with self._tmp_store(tmp_path):
            sks._calibration_cache.update(mtime=None, data={})
            sks.save_servo_calibration("gemma4:e4b", 1000, 1000,
                                       {"a_x": 0, "b_x": 0.1, "a_y": 0, "b_y": 1.0})
            assert sks.load_servo_calibration("gemma4:e4b", 1000, 1000) is None


class TestApply:
    def _servo(self, calibration):
        from backend.services.servo_controller import ServoController
        svc = ServoController.__new__(ServoController)
        svc._calibration = calibration
        svc.screen_w, svc.screen_h = 1000, 1000
        return svc

    def test_identity_without_calibration(self):
        assert self._servo(None)._apply_calibration(400, 300) == (400, 300)

    def test_inverts_measured_bias(self):
        # Measured live 2026-08-01: Y raw = +222.9 + 0.5777·truth. A dot truly at
        # y=126 produced raw≈296 → correction must recover ≈126.
        svc = self._servo({"a_x": 0.0, "b_x": 1.0, "a_y": 222.9, "b_y": 0.5777})
        x, y = svc._apply_calibration(500, 296)
        assert x == 500
        assert abs(y - 126) <= 2

    def test_clamped_to_screen(self):
        svc = self._servo({"a_x": 0.0, "b_x": 1.0, "a_y": 500.0, "b_y": 0.5})
        _, y = svc._apply_calibration(10, 990)   # (990-500)/0.5 = 980 ok; try beyond
        assert 0 <= y <= 999
        _, y2 = svc._apply_calibration(10, 20)   # (20-500)/0.5 = -960 → clamp 0
        assert y2 == 0


class TestFit:
    def test_recovers_known_line(self):
        truths = [100, 300, 500, 700, 900]
        raws = [223 + 0.578 * t for t in truths]
        a, b, n = fit_axis(truths, raws)
        assert abs(a - 223) < 1e-6 and abs(b - 0.578) < 1e-9 and n == 5

    def test_outlier_trimmed(self):
        truths = [100, 300, 500, 700, 900, 850]
        raws = [223 + 0.578 * t for t in truths[:-1]] + [50.0]  # wild outlier
        a, b, _ = fit_axis(truths, raws)
        assert abs(a - 223) < 20 and abs(b - 0.578) < 0.05
