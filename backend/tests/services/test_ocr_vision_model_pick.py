"""The OCR picker finds a vision model by what Ollama reports it can do.

Name patterns missed real vision models (no pattern matches a qwen3-vl tag),
so with a text-only chat model and only such a model installed, OCR reported
that no vision model existed. Ollama is faked at the HTTP seams the picker and
the capability resolver read (/api/show, /api/tags, /api/ps).
"""
from types import SimpleNamespace

import pytest
import requests

from backend.services import image_content_service as ics
from backend.services import model_capability_resolver as resolver
from backend.utils import ollama_resource_manager as orm

TEXT_ONLY = "llama3.1:8b"
QWEN_VL = "qwen3-vl:8b-thinking-q8_0"


class FakeOllama:
    def __init__(self, capabilities, resident=()):
        self.capabilities = capabilities  # tag -> Ollama capability list
        self.resident = list(resident)

    def post(self, url, json=None, timeout=None, **kw):
        tag = (json or {}).get("name")
        if tag not in self.capabilities:
            return SimpleNamespace(ok=False, status_code=404, json=lambda: {}, text="not found")
        body = {"capabilities": self.capabilities[tag], "model_info": {},
                "details": {"family": "test"}}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body)

    def get(self, url, timeout=None, **kw):
        names = self.resident if url.endswith("/api/ps") else list(self.capabilities)
        body = {"models": [{"name": t, "size": 4 * 1024 ** 3} for t in names]}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body,
                               raise_for_status=lambda: None)


@pytest.fixture
def ollama(monkeypatch):
    def install(capabilities, resident=()):
        fake = FakeOllama(capabilities, resident)
        shim = SimpleNamespace(post=fake.post, get=fake.get,
                               RequestException=requests.RequestException,
                               Timeout=requests.Timeout)
        monkeypatch.setattr(orm, "requests", shim)
        monkeypatch.setattr(ics, "requests", shim)
        # The resolver imports requests inside each call.
        monkeypatch.setattr(requests, "get", fake.get)
        return fake

    orm._model_info_cache.clear()
    orm._unreachable_at.clear()
    resolver.invalidate()
    yield install
    orm._model_info_cache.clear()
    orm._unreachable_at.clear()
    resolver.invalidate()


@pytest.fixture
def text_only_chat_model(monkeypatch):
    import llama_index.core
    monkeypatch.setattr(llama_index.core, "Settings",
                        SimpleNamespace(llm=SimpleNamespace(model=TEXT_ONLY)))


def _picker():
    extractor = ics.ImageContentExtractor()
    extractor.service_available = True
    return extractor


def test_a_qwen_vl_model_is_found_when_the_chat_model_is_text_only(ollama, text_only_chat_model):
    ollama({
        TEXT_ONLY: ["completion", "tools"],
        QWEN_VL: ["completion", "vision", "thinking"],
        "nomic-embed-text:latest": ["embedding"],
    })
    assert _picker()._get_available_vision_model() == QWEN_VL


def test_a_vision_model_already_in_memory_is_preferred(ollama, text_only_chat_model):
    ollama({
        TEXT_ONLY: ["completion"],
        QWEN_VL: ["completion", "vision"],
        "gemma4:12b": ["completion", "vision"],
    }, resident=["gemma4:12b"])
    assert _picker()._get_available_vision_model() == "gemma4:12b"


def test_no_vision_capability_means_no_pick(ollama, text_only_chat_model):
    # "llama3.2-vision-notes" would match a name pattern; Ollama says it cannot see.
    ollama({TEXT_ONLY: ["completion"], "llama3.2-vision-notes:latest": ["completion"]})
    assert _picker()._get_available_vision_model() is None
