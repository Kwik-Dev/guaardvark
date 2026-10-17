"""get_default_llm passes over a saved model Ollama says cannot chat.

/api/tags and the capability record are stand-ins (the record is built by
model_capabilities.record_from_info from the capability lists below).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import requests

import backend.config as cfg
from backend.services import model_capabilities as mc

CAPS = {
    "nimble:9b-q4_K_M": ["decision"],
    "embeddinggemma:latest": ["embedding"],
    "qwen3-vl:8b": ["completion", "vision", "tools"],
    "gemma4:e2b": ["completion", "vision", "tools", "thinking"],
    "qwen3:14b": ["completion", "tools"],
}


@pytest.fixture
def box(monkeypatch, tmp_path):
    state = {"installed": list(CAPS), "described": set(CAPS), "saved": None}

    def tags(url, timeout=None, **kwargs):
        body = {"models": [{"name": name} for name in state["installed"]]}
        return SimpleNamespace(status_code=200, ok=True, json=lambda: body)

    def record(tag, with_vision=True):
        info = {"capabilities": CAPS[tag]} if tag in state["described"] else None
        return mc.record_from_info(tag, info, with_vision=False)

    monkeypatch.setattr(requests, "get", tags)
    monkeypatch.setattr(mc, "capabilities_for", record)
    monkeypatch.setattr(mc, "LOCAL_ROWS_PATH", tmp_path / "model_capabilities.json")
    monkeypatch.setattr(cfg, "_read_saved_model_name", lambda: state["saved"])
    monkeypatch.setattr(cfg, "_saved_model_warned", set())
    monkeypatch.setenv("GUAARDVARK_DEFAULT_LLM", "fallback:1b")
    return state


@pytest.mark.parametrize("saved", ["nimble:9b-q4_K_M", "embeddinggemma:latest"])
def test_a_saved_model_that_cannot_chat_is_passed_over(box, saved, caplog):
    box["saved"] = saved

    with caplog.at_level(logging.WARNING, logger="backend.config"):
        assert cfg.get_default_llm() == "gemma4:e2b"
        assert cfg.get_default_llm() == "gemma4:e2b"

    warnings = [r.getMessage() for r in caplog.records if saved in r.getMessage()]
    assert len(warnings) == 1
    assert "cannot chat" in warnings[0] and "using gemma4:e2b" in warnings[0]


@pytest.mark.parametrize("saved", ["qwen3:14b", "qwen3-vl:8b", "gemma4:e2b"])
def test_a_saved_chat_model_wins_whatever_its_name(box, saved):
    """qwen3-vl matches the vision name rule that keeps it out of the default
    pick, but a person chose it and Ollama says it can chat."""
    box["saved"] = saved

    assert cfg.get_default_llm() == saved


def test_a_saved_model_ollama_cannot_describe_is_kept(box):
    box["saved"] = "nimble:9b-q4_K_M"
    box["described"] = set(CAPS) - {"nimble:9b-q4_K_M"}

    assert cfg.get_default_llm() == "nimble:9b-q4_K_M"


def test_nothing_that_can_chat_installed_gives_the_hardware_default(box):
    box["saved"] = "nimble:9b-q4_K_M"
    box["installed"] = ["nimble:9b-q4_K_M", "embeddinggemma:latest"]

    assert cfg.get_default_llm() == "fallback:1b"
