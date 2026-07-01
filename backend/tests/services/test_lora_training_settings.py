from backend.services.lora_training_settings import normalize_training_settings, DEFAULTS


def test_normalize_defaults():
    out = normalize_training_settings(None)
    assert out["resolution"] == 768
    assert out["rank"] == 16
    assert out["alpha"] == 16
    assert out["learning_rate"] == 1.0e-4
    assert out["steps"] is None


def test_normalize_clamps_and_snaps():
    out = normalize_training_settings({
        "resolution": 900,
        "rank": 2,
        "alpha": 200,
        "learning_rate": 5e-5,
        "steps": 800,
    })
    assert out["resolution"] == 896  # snapped to /64
    assert out["rank"] == 4
    assert out["alpha"] == 128
    assert out["learning_rate"] == 5e-5
    assert out["steps"] == 800


def test_normalize_ignores_none_overrides():
    out = normalize_training_settings({"steps": None, "resolution": None})
    assert out["resolution"] == DEFAULTS["resolution"]
    assert out["steps"] is None