"""Provider lookup.

Modules are imported lazily and independently so a provider whose optional
dependency is missing degrades to "unavailable" instead of breaking the whole
catalog.
"""

from __future__ import annotations

import importlib
import logging
from types import ModuleType
from typing import Dict, List, Optional

from backend.services.connections.base import ProviderSpec

logger = logging.getLogger(__name__)

# provider id -> module name under backend.services.connections.providers
PROVIDER_MODULES: Dict[str, str] = {
    "discord_webhook": "discord_webhook",
    "mastodon": "mastodon",
    "telegram": "telegram",
    "bluesky": "bluesky",
    "youtube": "youtube",
}

_cache: Dict[str, ModuleType] = {}


def get_provider(provider: str) -> ModuleType:
    """Import and return a provider module."""
    key = (provider or "").strip().lower()
    if key in _cache:
        return _cache[key]
    module_name = PROVIDER_MODULES.get(key)
    if not module_name:
        raise KeyError(f"Unknown provider '{provider}'.")
    module = importlib.import_module(
        f"backend.services.connections.providers.{module_name}"
    )
    _cache[key] = module
    return module


def spec_for(provider: str) -> ProviderSpec:
    return get_provider(provider).SPEC


def list_specs(family: Optional[str] = None) -> List[ProviderSpec]:
    """Every loadable provider spec, optionally filtered by family."""
    specs: List[ProviderSpec] = []
    for provider in PROVIDER_MODULES:
        try:
            spec = spec_for(provider)
        except Exception as e:  # noqa: BLE001 - one bad provider must not hide the rest
            logger.warning("Provider '%s' failed to load: %s", provider, e)
            continue
        if family and spec.family != family:
            continue
        specs.append(spec)
    return sorted(specs, key=lambda s: s.label.lower())


def social_platforms() -> List[str]:
    """Provider ids in the social family, for cadence bookkeeping."""
    return [s.provider for s in list_specs("social")]
