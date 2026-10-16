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

from backend.services.connections.base import FAMILY_SOCIAL, ProviderSpec

logger = logging.getLogger(__name__)

# provider id -> module name under backend.services.connections.providers
PROVIDER_MODULES: Dict[str, str] = {
    "discord_webhook": "discord_webhook",
    "mastodon": "mastodon",
    "telegram": "telegram",
    "bluesky": "bluesky",
    "youtube": "youtube",
    "linkedin": "linkedin",
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


def contract_problems(provider: str) -> List[str]:
    """Where a provider's declarations and functions disagree (see base.py).

    An empty list means the module honours the contract. The publish path
    also checks for the function itself before calling it, so a mismatch
    refuses the action rather than raising.
    """
    module = get_provider(provider)
    spec = module.SPEC
    caps = spec.capabilities
    problems: List[str] = []

    def missing(name: str) -> bool:
        return not callable(getattr(module, name, None))

    if missing("test"):
        problems.append("test() is required.")
    if spec.family == FAMILY_SOCIAL and missing("publish"):
        problems.append("publish() is required for a social provider.")
    if caps.native_scheduling is not None:
        for name in ("schedule", "scheduled_state"):
            if missing(name):
                problems.append(f"native_scheduling is declared but {name}() is missing.")
    elif not missing("schedule"):
        problems.append("schedule() exists but native_scheduling is not declared.")
    if caps.deletable and missing("delete"):
        problems.append("deletable is declared but delete() is missing.")
    if not caps.deletable and not missing("delete"):
        problems.append("delete() exists but deletable is not declared.")
    for limit in caps.rate_limits:
        if limit.posts < 1 or limit.window_seconds < 1:
            problems.append(f"rate limit {limit} must allow at least one post per window.")
        if not limit.source:
            problems.append("every rate limit names the platform page it is kept below.")
    if spec.family == FAMILY_SOCIAL and not caps.rate_limits:
        problems.append("a social provider declares at least one rate limit.")
    return problems


def supports(provider: str, function: str) -> bool:
    """Whether the provider module defines ``function``."""
    try:
        return callable(getattr(get_provider(provider), function, None))
    except KeyError:
        return False
