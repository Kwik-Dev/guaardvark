"""
LLM provider selection.

Guaardvark defaults to local Ollama. When a Mistral API key is configured the
user can flip a runtime toggle (persisted in the ``settings`` table) so chat
generation routes to Mistral instead. This module is the single source of truth
for "which provider is active" and "which model within that provider".

Embeddings deliberately are NOT covered here — they always stay on Ollama so the
RAG vector store stays consistent with what indexed it.
"""

from __future__ import annotations

import logging
from typing import Optional

from backend import config

logger = logging.getLogger(__name__)

OLLAMA = "ollama"
MISTRAL = "mistral"

_PROVIDER_KEY = "llm_provider"
_MISTRAL_MODEL_KEY = "mistral_active_model"


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


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
def mistral_available() -> bool:
    return bool(config.MISTRAL_API_KEY)


def get_active_provider() -> str:
    """Return the active provider, falling back to Ollama.

    A stored ``mistral`` choice degrades to ``ollama`` if the key was removed, so
    a missing key can never wedge the chat into an unusable provider.
    """
    provider = (_get_setting(_PROVIDER_KEY) or OLLAMA).strip().lower()
    if provider == MISTRAL and not mistral_available():
        logger.warning("Provider 'mistral' selected but no API key set; using Ollama.")
        return OLLAMA
    return provider if provider in (OLLAMA, MISTRAL) else OLLAMA


def set_active_provider(provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider not in (OLLAMA, MISTRAL):
        raise ValueError(f"Unknown provider '{provider}' (expected 'ollama' or 'mistral').")
    if provider == MISTRAL and not mistral_available():
        raise ValueError("Cannot select Mistral: MISTRAL_API_KEY is not configured.")
    _set_setting(_PROVIDER_KEY, provider)
    logger.info("LLM provider set to '%s'", provider)
    return provider


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
