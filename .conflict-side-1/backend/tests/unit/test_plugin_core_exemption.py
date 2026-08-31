"""Tests for the core-plugin circuit-breaker exemption in PluginManager.

A *core* pillar (e.g. ollama — the inference backbone) must never have its
circuit breaker tripped: the whole workstation depends on it, so locking it
out of auto-restore is self-defeating. The exemption lives in
``PluginManager._fail_plugin_start``, which is the single choke point that
funnels a failed start into ``record_start_failure`` (and thus toward the
breaker threshold).

These tests exercise both sides of the guard — the core plugin is spared,
and a non-core plugin's breaker still trips (zero-placebo: the guard proves
its negative case)."""

from types import SimpleNamespace

from backend.plugins.plugin_manager import PluginManager
from backend.plugins.plugin_state_store import PluginStateStore


def _stub_manager(tmp_path, plugins):
    """Build a minimal object carrying just the two collaborators
    ``_fail_plugin_start`` reads, so we can call it as an unbound method
    without booting the full manager (discovery, restore, sockets...)."""
    store = PluginStateStore(tmp_path / "plugin_state.json")
    registry = SimpleNamespace(get_plugin=lambda pid: plugins.get(pid))
    return SimpleNamespace(state_store=store, registry=registry), store


def test_core_plugin_breaker_never_trips_however_many_times_it_fails(tmp_path):
    stub, store = _stub_manager(tmp_path, {"ollama": SimpleNamespace(core=True)})

    # Far past the default threshold (4) — a core pillar never accrues.
    for _ in range(10):
        PluginManager._fail_plugin_start(stub, "ollama", {"success": False})

    assert store.is_breaker_tripped("ollama") is False
    # And it never even started counting toward the threshold.
    assert store.snapshot()["start_failure_counts"].get("ollama", 0) == 0


def test_non_core_plugin_breaker_still_trips_at_threshold(tmp_path):
    # Negative case: the guard is specific to core. A disposable plugin must
    # still be damped exactly as before.
    stub, store = _stub_manager(tmp_path, {"comfyui": SimpleNamespace(core=False)})

    for _ in range(4):  # default threshold
        PluginManager._fail_plugin_start(stub, "comfyui", {"success": False})

    assert store.is_breaker_tripped("comfyui") is True


def test_unknown_plugin_falls_through_to_normal_counting(tmp_path):
    # get_plugin -> None must not crash and must behave as non-core.
    stub, store = _stub_manager(tmp_path, {})

    for _ in range(4):
        PluginManager._fail_plugin_start(stub, "ghost", {"success": False})

    assert store.is_breaker_tripped("ghost") is True
