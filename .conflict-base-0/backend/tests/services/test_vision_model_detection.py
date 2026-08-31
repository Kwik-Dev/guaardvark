"""Vision model detection must never pick text-only VRAM residents."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.services.servo_knowledge_store import (
    get_vision_config,
    model_name_looks_vision,
)


def test_text_only_qwen_does_not_look_vision():
    assert not model_name_looks_vision("jaahas/qwen3.5-uncensored:latest")
    assert not model_name_looks_vision("qwen3.5:9b")
    assert not model_name_looks_vision("llama3:latest")


def test_known_vision_names_look_vision():
    assert model_name_looks_vision("gemma4:e4b")
    assert model_name_looks_vision("moondream:latest")
    assert model_name_looks_vision("qwen3-vl:4b-instruct")
    assert model_name_looks_vision("qwen2.5vl:7b-q4_K_M")


def test_unknown_text_model_config_has_no_vision():
    cfg = get_vision_config("jaahas/qwen3.5-uncensored:latest")
    assert cfg["has_vision"] is False
    assert cfg.get("vision_model")  # external eyes


def test_detect_skips_text_only_active_for_gemma4():
    from backend.utils.vision_analyzer import VisionAnalyzer

    ps = MagicMock()
    ps.status_code = 200
    ps.json.return_value = {
        "models": [{"name": "jaahas/qwen3.5-uncensored:latest"}],
    }
    tags = MagicMock()
    tags.status_code = 200
    tags.json.return_value = {
        "models": [
            {"name": "jaahas/qwen3.5-uncensored:latest"},
            {"name": "gemma4:e4b"},
            {"name": "moondream:latest"},
        ],
    }

    def _get(url, **_kwargs):
        if url.endswith("/api/ps"):
            return ps
        return tags

    with patch("backend.utils.vision_analyzer.requests.get", side_effect=_get):
        az = VisionAnalyzer(ollama_url="http://localhost:11434")
        assert az.default_model == "gemma4:e4b"
