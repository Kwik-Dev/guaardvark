"""
LLM provider selection — local-first with an opt-in cloud layer.

Guaardvark defaults to local Ollama and stays 100% offline unless the operator
explicitly opts in. Two gates, in order:

1. A MASTER switch ``cloud_models_enabled`` (DB setting, default OFF) — the
   single "airplane mode" flip that allows ANY cloud provider at all. While it
   is off, ``get_active_provider()`` always returns Ollama regardless of what
   else is configured, so a fresh install is fully local until the operator
   turns this on.
2. A per-provider selection (currently Ollama | Mistral) plus that provider's
   API key being present.

Adding a new cloud provider later is a matter of extending ``CLOUD_PROVIDERS``
and giving it a client module shaped like ``mistral_provider`` — the master
toggle, the UI indicator, and the API all key off this registry.

Embeddings are deliberately NOT covered here — they ALWAYS stay on Ollama so the
RAG vector store stays consistent with what indexed it, even when chat is cloud.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Dict, List, Optional

from backend import config

logger = logging.getLogger(__name__)

OLLAMA = "ollama"
MISTRAL = "mistral"
OPENAI = "openai"

_PROVIDER_KEY = "llm_provider"
_MISTRAL_MODEL_KEY = "mistral_active_model"
_OPENAI_MODEL_KEY = "openai_active_model"
_CLOUD_ENABLED_KEY = "cloud_models_enabled"

# Deployment-level defaults. The DB settings above are the operator's runtime toggles;
# these env vars let a deployment that IS cloud by default (cloud-plus) say so once in
# .env, instead of depending on two DB rows that a fresh clone or a DB reset loses.
# They are still an explicit opt-in: the point is that consent is stated deliberately,
# not inferred from GUAARDVARK_OPENAI_BASE_URL, which only supplies an endpoint.
_CLOUD_ENABLED_ENV = "GUAARDVARK_CLOUD_MODELS_ENABLED"
_PROVIDER_ENV = "GUAARDVARK_LLM_PROVIDER"

_TRUTHY = ("1", "true", "yes", "on")
_FALSY = ("0", "false", "no", "off")


def _env_flag(name: str) -> Optional[bool]:
    """Tri-state env read: True/False when set to a recognized boolean, else None.

    Unset means "no opinion" (fall through to the DB). A set-but-unparseable value is
    also None — with a warning — rather than being read as False, which would look like
    a deliberate local-only choice.
    """
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    logger.warning("%s=%r is not a boolean; ignoring the env override", name, raw)
    return None


def _env_provider() -> str:
    """The provider pinned by GUAARDVARK_LLM_PROVIDER, or "" when unset/unknown."""
    raw = (os.environ.get(_PROVIDER_ENV) or "").strip().lower()
    if not raw:
        return ""
    if raw != OLLAMA and raw not in CLOUD_PROVIDERS:
        logger.warning("%s=%r is not a known provider; ignoring it", _PROVIDER_ENV, raw)
        return ""
    return raw


# ---------------------------------------------------------------------------
# Cloud provider registry. id -> metadata. `available` is computed live from
# whether the provider's API key is configured. Add a provider here (+ a client
# module) and the master toggle / UI indicator / API pick it up automatically.
# ---------------------------------------------------------------------------
def _mistral_available() -> bool:
    return bool(config.MISTRAL_API_KEY)


def _openai_available() -> bool:
    # Single source of truth: openai_provider.available(). This provider is
    # available when GUAARDVARK_OPENAI_BASE_URL is set; GUAARDVARK_OPENAI_API_KEY
    # is optional (local vLLM / Ollama need none). A bare OPENAI_API_KEY must
    # never enable a cloud route.
    from backend.services import openai_provider
    return openai_provider.available()


CLOUD_PROVIDERS: Dict[str, Dict] = {
    MISTRAL: {
        "label": "Mistral (cloud)",
        "key_env": "MISTRAL_API_KEY",
        "required_env": "MISTRAL_API_KEY",
        "available_fn": _mistral_available,
    },
    OPENAI: {
        "label": "OpenAI-compatible (cloud)",
        # key_env is the optional bearer token; required_env is what actually
        # makes the provider available, so error messages name the right var.
        "key_env": "GUAARDVARK_OPENAI_API_KEY",
        "required_env": "GUAARDVARK_OPENAI_BASE_URL",
        "available_fn": _openai_available,
    },
}


# ---------------------------------------------------------------------------
# Generic settings access (mirrors llm_service's Setting usage)
# ---------------------------------------------------------------------------
def _get_setting(key: str) -> Optional[str]:
    try:
        from backend.models import Setting, db

        if db and Setting:
            row = db.session.get(Setting, key)
            if row and row.value:
                return row.value
    except Exception as e:  # noqa: BLE001 - best effort, no Flask ctx etc.
        logger.debug("llm_provider: could not read setting %s: %s", key, e)
    return None


def _set_setting(key: str, value: str) -> bool:
    try:
        from backend.models import Setting, db

        if not (db and Setting):
            return False
        row = db.session.get(Setting, key)
        if row:
            row.value = value
        else:
            db.session.add(Setting(key=key, value=value))
        db.session.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_provider: could not write setting %s: %s", key, e)
        try:
            from backend.models import db
            db.session.rollback()
        except Exception:
            pass
        return False


@contextlib.contextmanager
def app_context_if_needed():
    """An app context only when the caller has none — for worker-thread consent reads.

    Every gate in this module reads a DB ``Setting`` row. ``db.session`` raises outside
    an app context, ``_get_setting`` swallows that and reports "unset", and every gate
    here reads "unset" as NO CONSENT — silently, and regardless of what the operator
    actually chose. So a bare worker thread (``BatchImageGenerator._queue_worker``, a
    Celery task) sees ``cloud_models_enabled() == False`` and ``is_openai_active() ==
    False`` no matter what the Settings page says.

    Keeping the no-context default fail-closed to local is DELIBERATE — it is pinned by
    ``test_default_llm_gate.test_worker_without_db_context_stays_local`` — so it is not
    changed here. A caller that must genuinely honor the operator's choice (a batch
    image/video worker rather than a request handler) opts in by wrapping its read:

        with llm_provider.app_context_if_needed():
            consented = llm_provider.is_openai_active()

    No-op when an app context already exists, so request-path callers are unaffected.
    """
    try:
        from flask import has_app_context
        if has_app_context():
            yield
            return
    except Exception:  # noqa: BLE001 — no Flask at all: nothing to push
        yield
        return
    try:
        from backend.app import app as _app
    except Exception:  # noqa: BLE001 — no app singleton (tests, early import)
        _app = None
    if _app is None:
        yield
        return
    with _app.app_context():
        yield


# ---------------------------------------------------------------------------
# Master cloud switch (the airplane-mode gate)
# ---------------------------------------------------------------------------
def cloud_models_env_forced() -> bool:
    """True when the master switch is pinned by the environment, not the DB.

    The Settings toggle cannot override it; ``provider_state()`` reports this so the page
    can show the switch as forced instead of showing a stored value the gate ignores.
    Mirrors ``media_director.verbatim_prompts_env_forced``.
    """
    return _env_flag(_CLOUD_ENABLED_ENV) is not None


def cloud_models_enabled() -> bool:
    """Master switch. Default OFF — a fresh install is fully local until the
    operator opts in. Everything cloud is gated behind this.

    ``GUAARDVARK_CLOUD_MODELS_ENABLED=true`` pins it ON from the environment, so a
    deployment that is cloud by default does not depend on a DB row: the env default is
    the deployment's stated intent and wins over the stored value. ``=false`` pins it OFF
    the same way, and still overrides a stored ``true``.
    """
    forced = _env_flag(_CLOUD_ENABLED_ENV)
    if forced is not None:
        return forced
    return (_get_setting(_CLOUD_ENABLED_KEY) or "").strip().lower() in _TRUTHY


def set_cloud_models_enabled(enabled: bool) -> bool:
    _set_setting(_CLOUD_ENABLED_KEY, "true" if enabled else "false")
    if cloud_models_env_forced():
        logger.warning(
            "Cloud models stored as %s, but %s pins them to %s — the env default wins.",
            enabled, _CLOUD_ENABLED_ENV, cloud_models_enabled(),
        )
        return cloud_models_enabled()
    logger.info("Cloud models %s", "ENABLED" if enabled else "disabled")
    return bool(enabled)


def provider_available(provider: str) -> bool:
    """True when the provider's consent env is configured (independent of the master switch)."""
    meta = CLOUD_PROVIDERS.get((provider or "").strip().lower())
    return bool(meta and meta["available_fn"]())


# Back-compat alias used by the API/older callers.
def mistral_available() -> bool:
    return provider_available(MISTRAL)


# ---------------------------------------------------------------------------
# Active provider
# ---------------------------------------------------------------------------
def get_active_provider() -> str:
    """Return the active chat provider.

    Hard gate: if the master cloud switch is OFF, ALWAYS Ollama. Otherwise the
    env-pinned provider (``GUAARDVARK_LLM_PROVIDER``) or the stored choice, degrading
    back to Ollama if its key was removed — so a missing key or a flipped-off master
    switch can never wedge chat into a dead provider.
    """
    if not cloud_models_enabled():
        return OLLAMA
    pinned = _env_provider()
    if pinned:
        provider = pinned
    else:
        provider = (_get_setting(_PROVIDER_KEY) or OLLAMA).strip().lower()
    if provider == OLLAMA:
        return OLLAMA
    if provider in CLOUD_PROVIDERS and provider_available(provider):
        return provider
    if provider in CLOUD_PROVIDERS:
        logger.warning("Provider '%s' selected but no API key set; using Ollama.", provider)
    return OLLAMA


def set_active_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider != OLLAMA and provider not in CLOUD_PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'.")
    if provider != OLLAMA:
        if not cloud_models_enabled():
            raise ValueError("Cloud models are disabled. Enable them first (master toggle).")
        if not provider_available(provider):
            meta = CLOUD_PROVIDERS[provider]
            env = meta.get("required_env") or meta["key_env"]
            raise ValueError(f"Cannot select {provider}: {env} is not configured.")
    _set_setting(_PROVIDER_KEY, provider)
    if (os.environ.get(_PROVIDER_ENV) or "").strip():
        logger.warning(
            "%s pins the provider to '%s'; the stored selection '%s' will not take effect.",
            _PROVIDER_ENV, _env_provider() or "", provider,
        )
    logger.info("LLM provider set to '%s'", provider)
    if provider == OPENAI:
        from backend.services import openai_provider
        logger.info("OpenAI-compatible chat route: %s", openai_provider.describe(get_openai_model()))
    return provider


def cloud_active() -> bool:
    """True when a cloud (non-Ollama) provider is the active chat model. The UI
    uses this to show the 'data leaves your machine' indicator."""
    return get_active_provider() != OLLAMA


# ---------------------------------------------------------------------------
# Mistral model selection
# ---------------------------------------------------------------------------
def get_mistral_model() -> str:
    return _get_setting(_MISTRAL_MODEL_KEY) or config.MISTRAL_DEFAULT_MODEL


def set_mistral_model(model: str) -> str:
    model = (model or "").strip()
    if not model:
        raise ValueError("Mistral model name cannot be empty.")
    _set_setting(_MISTRAL_MODEL_KEY, model)
    return model


def is_mistral_active() -> bool:
    return get_active_provider() == MISTRAL


# ---------------------------------------------------------------------------
# OpenAI-compatible model selection
# ---------------------------------------------------------------------------
def get_openai_model() -> str:
    return _get_setting(_OPENAI_MODEL_KEY) or config.OPENAI_DEFAULT_MODEL


def set_openai_model(model: str) -> str:
    model = (model or "").strip()
    if not model:
        raise ValueError("OpenAI model name cannot be empty.")
    _set_setting(_OPENAI_MODEL_KEY, model)
    return model


def is_openai_active() -> bool:
    return get_active_provider() == OPENAI


def get_active_cloud_model() -> Optional[str]:
    """Return the model name for the active cloud provider, or None on Ollama."""
    p = get_active_provider()
    if p == MISTRAL:
        return get_mistral_model()
    if p == OPENAI:
        return get_openai_model()
    return None


# ---------------------------------------------------------------------------
# UI/state snapshot
# ---------------------------------------------------------------------------
def provider_state() -> Dict:
    """Everything the settings UI needs in one call."""
    enabled = cloud_models_enabled()
    providers: List[Dict] = [{"id": OLLAMA, "label": "Ollama (local)", "available": True, "cloud": False}]
    for pid, meta in CLOUD_PROVIDERS.items():
        providers.append({
            "id": pid,
            "label": meta["label"],
            "available": meta["available_fn"](),
            "key_env": meta["key_env"],
            "required_env": meta.get("required_env") or meta["key_env"],
            "cloud": True,
        })
    return {
        "cloud_models_enabled": enabled,
        # The Settings toggle is inert while the env default is set; the page shows it as
        # forced rather than as a value the gate ignores (verbatim_prompts precedent).
        "cloud_models_env_forced": cloud_models_env_forced(),
        "cloud_models_env_var": _CLOUD_ENABLED_ENV,
        "provider_env_forced": bool(_env_provider()),
        "provider_env_var": _PROVIDER_ENV,
        "provider": get_active_provider(),
        "cloud_active": cloud_active(),
        "mistral_model": get_mistral_model(),
        "openai_model": get_openai_model(),
        "providers": providers,
    }
