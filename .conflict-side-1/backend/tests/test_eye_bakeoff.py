"""Eye bake-off harness — calibration image ground truth (no models)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.tools.eye_bakeoff import make_calibration_image, TARGETS  # noqa: E402


def test_calibration_ground_truth_matches_fractions():
    size = 1000
    img, truth = make_calibration_image(size)
    assert img.size == (size, size)
    assert len(truth) == len(TARGETS)
    # Ground-truth centers are the target fractions * size.
    for (label, rgb, (fx, fy)), (tlabel, (cx, cy)) in zip(TARGETS, truth):
        assert tlabel == label
        assert cx == int(fx * size)
        assert cy == int(fy * size)


def test_calibration_scales_with_size():
    _, truth = make_calibration_image(500)
    labels = {t[0] for t in truth}
    assert "green circle" in labels
    # green circle is centered (0.5, 0.5) → (250, 250) at size 500
    green = dict(truth)["green circle"]
    assert green == (250, 250)
