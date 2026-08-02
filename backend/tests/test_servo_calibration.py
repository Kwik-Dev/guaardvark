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
        assert got == {"a_x": -15.7, "b_x": 1.0412, "a_y": 222.9, "b_y": 0.5777,
                       "model": "linear"}  # loader stamps the family (legacy = linear)

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


class TestModelFamilies:
    """Wave 2: radial (Dean's X-leg insight) + piecewise_y apply and store."""

    def _servo(self, calibration):
        from backend.services.servo_controller import ServoController
        svc = ServoController.__new__(ServoController)
        svc._calibration = calibration
        svc.screen_w, svc.screen_h = 1000, 1000
        return svc

    def test_radial_apply_pushes_back_along_spoke(self):
        # k=0.7: eye pulls 30% toward center. Raw at (780,300) on the spoke
        # toward a true corner target — correction must push it back outward.
        svc = self._servo({"model": "radial", "k": 0.7, "cx": 500.0, "cy": 500.0})
        x, y = svc._apply_calibration(780, 300)
        assert x == 900 and y == 214  # C + (raw-C)/k

    def test_piecewise_identity_below_elbow(self):
        svc = self._servo({"model": "piecewise_y", "elbow": 400.0, "a_y": 207.0, "b_y": 0.68})
        assert svc._apply_calibration(600, 700) == (600, 700)  # untouched zone
        _, y = svc._apply_calibration(600, 350)
        assert y == 210  # (350-207)/0.68 ≈ 210 — corrected zone

    def test_store_roundtrip_radial(self, tmp_path):
        import backend.services.servo_knowledge_store as sks
        from unittest.mock import patch
        with patch.object(sks, "_CALIBRATION_PATH", tmp_path / "c.json"):
            sks._calibration_cache.update(mtime=None, data={})
            sks.save_servo_calibration("m", 1000, 1000,
                                       {"model": "radial", "k": 0.72, "cx": 500, "cy": 500})
            got = sks.load_servo_calibration("m", 1000, 1000)
        assert got == {"model": "radial", "k": 0.72, "cx": 500.0, "cy": 500.0}

    def test_store_refuses_degenerate_radial_gain(self, tmp_path):
        import backend.services.servo_knowledge_store as sks
        from unittest.mock import patch
        with patch.object(sks, "_CALIBRATION_PATH", tmp_path / "c.json"):
            sks._calibration_cache.update(mtime=None, data={})
            sks.save_servo_calibration("m", 1000, 1000,
                                       {"model": "radial", "k": 0.1, "cx": 500, "cy": 500})
            assert sks.load_servo_calibration("m", 1000, 1000) is None


class TestGatedFit:
    """The validation gate: candidates only win by beating identity."""

    def _pairs_radial(self, k=0.7, n=6):
        # Synthetic world where the eye truly is radial with gain k.
        pts = [(900, 150), (150, 150), (900, 850), (150, 850), (750, 300), (300, 700)][:n]
        out = []
        for tx, ty in pts:
            rx = 500 + k * (tx - 500)
            ry = 500 + k * (ty - 500)
            out.append(((float(tx), float(ty)), (rx, ry), (1000, 1000)))
        return out

    def test_fit_radial_recovers_gain(self):
        from backend.tools.servo_calibrate import fit_radial
        cand = fit_radial(self._pairs_radial(k=0.7))
        assert cand["model"] == "radial"
        assert abs(cand["k"] - 0.7) < 0.01

    def test_radial_candidate_beats_identity_on_radial_world(self):
        from backend.tools.servo_calibrate import fit_radial, evaluate_candidate
        pairs = self._pairs_radial(k=0.7)
        cand = fit_radial(pairs)
        id_err, id_catch = evaluate_candidate(None, pairs)
        c_err, c_catch = evaluate_candidate(cand, pairs)
        assert c_err < id_err and c_catch >= id_catch
        assert c_err < 2.0  # near-perfect on its own world

    def test_position_split_never_leaks_a_position(self):
        from backend.tools.servo_calibrate import split_by_position
        pairs = self._pairs_radial(n=6) * 3  # 3 rows per position
        train, heldout = split_by_position(pairs)
        tp = {(round(p[0][0]), round(p[0][1])) for p in train}
        hp = {(round(p[0][0]), round(p[0][1])) for p in heldout}
        assert tp.isdisjoint(hp) and heldout
