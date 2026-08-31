"""require_hub_files refuses a load when the HF cache is empty."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from backends.hub_weights import WeightsNotInstalled, require_hub_files  # noqa: E402


def test_require_hub_files_raises_when_missing(monkeypatch):
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda *a, **k: None,
    )
    with pytest.raises(WeightsNotInstalled, match="Manage models"):
        require_hub_files("org/repo", ["config.json"], "Test model")


def test_require_hub_files_passes_when_cached(monkeypatch):
    monkeypatch.setattr(
        "huggingface_hub.try_to_load_from_cache",
        lambda *a, **k: "/tmp/cache/config.json",
    )
    require_hub_files("org/repo", ["config.json"], "Test model")
