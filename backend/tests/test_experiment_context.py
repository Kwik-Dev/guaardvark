"""Tests for thread-local experiment config injection."""
import threading
from backend.utils.experiment_context import (
    set_experiment_config,
    get_experiment_config,
    clear_experiment_config,
)


def test_get_returns_none_by_default():
    clear_experiment_config()
    assert get_experiment_config() is None


def test_set_and_get_config():
    config = {"top_k": 10, "dedup_threshold": 0.75}
    set_experiment_config(config)
    assert get_experiment_config() == config
    clear_experiment_config()


def test_clear_removes_config():
    set_experiment_config({"top_k": 10})
    clear_experiment_config()
    assert get_experiment_config() is None


def test_boolean_params_parse_strictly():
    from backend.utils.experiment_context import _clamp_params
    for raw, expected in [
        (True, True), (False, False), ("true", True), ("false", False),
        ("False", False), ("1", True), ("0", False), ("yes", True), ("no", False),
        (1, True), (0, False),
    ]:
        assert _clamp_params({"reranking_enabled": raw}) == {"reranking_enabled": expected}, raw


def test_unrecognised_boolean_is_dropped(caplog):
    from backend.utils.experiment_context import _clamp_params
    assert _clamp_params({"query_expansion": "maybe", "top_k": 5}) == {"top_k": 5}
    assert "query_expansion" in caplog.text


def test_experiment_false_string_reaches_the_overlay_as_false():
    from backend.utils.experiment_context import (
        get_active_rag_params, invalidate_active_params_cache,
    )
    invalidate_active_params_cache()
    set_experiment_config({"reranking_enabled": "false"})
    try:
        assert get_active_rag_params()["reranking_enabled"] is False
    finally:
        clear_experiment_config()
        invalidate_active_params_cache()


def test_thread_isolation():
    """Config set in one thread is not visible in another."""
    set_experiment_config({"top_k": 10})
    result = {}

    def check_other_thread():
        result["config"] = get_experiment_config()

    t = threading.Thread(target=check_other_thread)
    t.start()
    t.join()
    assert result["config"] is None
    clear_experiment_config()
