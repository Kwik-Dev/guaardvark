"""One capability record per Ollama model, and the checks that now read it.

Ollama is faked at the HTTP seam ollama_resource_manager reads (/api/show,
/api/tags); get_model_info, the record, the tools and thinking helpers, the
resolver's ModelProfile and the report script run for real. The old
implementations of the migrated checks are kept here as references, so the
record is shown to answer exactly as they did.
"""
import itertools
import json
import re
from types import SimpleNamespace

import pytest
import requests

from backend.services import model_capabilities as mc
from backend.services import model_capability_resolver as resolver
from backend.utils import ollama_resource_manager as orm


class FakeOllama:
    """/api/show and /api/tags for a set of tags; ``down`` refuses every call."""

    def __init__(self, models=None):
        self.models = models or {}
        self.down = False
        self.shows = []

    def _check(self):
        if self.down:
            raise requests.ConnectionError("connection refused")

    def post(self, url, json=None, timeout=None, **kw):
        self._check()
        tag = (json or {}).get("name")
        self.shows.append(tag)
        if tag not in self.models:
            return SimpleNamespace(ok=False, status_code=404, json=lambda: {})
        m = self.models[tag]
        body = {"capabilities": m.get("capabilities", []), "model_info": m.get("model_info", {}),
                "details": m.get("details", {"family": m.get("family", "llama")})}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body)

    def get(self, url, timeout=None, **kw):
        self._check()
        body = {"models": [{"name": t, "size": 4 * 1024 ** 3} for t in self.models]}
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body, raise_for_status=lambda: None)


@pytest.fixture
def ollama(monkeypatch, tmp_path):
    fake = FakeOllama()
    monkeypatch.setattr(orm, "requests", SimpleNamespace(
        post=fake.post, get=fake.get, RequestException=requests.RequestException,
    ))
    orm._model_info_cache.clear()
    orm._unreachable_at.clear()
    resolver.invalidate()
    monkeypatch.setattr(mc, "LOCAL_ROWS_PATH", tmp_path / "model_capabilities.json")
    mc._local_cache.update(mtime=None, rows={})
    yield fake
    orm._model_info_cache.clear()
    orm._unreachable_at.clear()
    resolver.invalidate()


CAP_SETS = [[], ["completion"], ["completion", "tools"], ["completion", "thinking"],
            ["completion", "tools", "thinking", "vision"], ["embedding"]]
TAGS = ["gemma4:e4b", "qwen3.5:9b", "deepseek-r1:8b", "llama3.1:8b", "mistral:7b",
        "qwen3-embedding:4b", "nomic-embed-text:latest", "my-custom:latest"]


# ── the old implementations, kept as references ──────────────────────────────

OLD_THINKING = [r'deepseek-r1', r'thinking', r'gemma[\-_]?4', r'qwen3']


def _old_tools(tag, info):
    if not info:
        return False
    return "tools" in info.get("capabilities", [])


def _old_thinking(tag, info):
    if not tag:
        return False
    if any(re.search(p, tag.lower()) for p in OLD_THINKING):
        return True
    if not info:
        return False
    return "thinking" in (info.get("capabilities") or [])


def _expected_thinking(tag, info):
    """The old answer, except that an embedding model is no longer a thinker by name."""
    if tag and re.search(r"embed|rerank", tag.lower()):
        return bool(info) and "thinking" in (info.get("capabilities") or [])
    return _old_thinking(tag, info)


def test_the_name_rules_are_unchanged():
    assert orm.THINKING_MODEL_PATTERNS == OLD_THINKING
    assert orm.VISION_MODEL_PATTERNS == [
        r'vl\b', r'vision', r'llava', r'moondream', r'bakllava', r'minicpm-v',
        r'llama.*vision', r'granite.*vision', r'gemma.*vision', r'gemma[\-_]?4']
    assert orm.NON_TEXT_MODEL_PATTERNS == [
        r'vl\b', r'vision', r'llava', r'moondream', r'bakllava', r'minicpm-v',
        r'llama.*vision', r'granite.*vision', r'gemma.*vision', r'embed', r'retrieval', r'minilm']


@pytest.mark.parametrize("caps", CAP_SETS, ids=lambda c: "+".join(c) or "none")
def test_tools_and_thinking_answer_as_before(ollama, caps):
    ollama.models = {t: {"capabilities": caps} for t in TAGS}
    for tag in TAGS:
        info = orm.get_model_info(tag)
        assert orm.model_supports_tools(tag) == _old_tools(tag, info), tag
        assert orm.model_supports_thinking(tag) == _expected_thinking(tag, info), tag
        assert orm.think_payload(tag) == ({"think": False} if _expected_thinking(tag, info) else {})


def test_tools_and_thinking_with_ollama_down(ollama):
    ollama.down = True
    for tag in TAGS + ["", None]:
        assert orm.model_supports_tools(tag or "") is False
        assert orm.model_supports_thinking(tag or "") == _expected_thinking(tag or "", None)


def test_a_name_match_still_decides_thinking_without_io(ollama):
    ollama.models = {"gemma4:12b": {"capabilities": ["completion", "thinking"]}}
    assert orm.model_supports_thinking("gemma4:12b") is True
    assert ollama.shows == []


# ── the record ───────────────────────────────────────────────────────────────

def test_the_record_reads_api_show(ollama):
    ollama.models = {
        "qwen3-embedding:4b": {"capabilities": ["embedding"], "family": "qwen3",
                               "model_info": {"qwen3.embedding_length": 2560, "qwen3.context_length": 40960}},
        "gemma4:e4b": {"capabilities": ["completion", "vision", "tools", "thinking"], "family": "gemma4",
                       "model_info": {"gemma4.context_length": 131072,
                                      "gemma4.rope.scaling.original_context_length": 8192,
                                      "gemma4.audio.embedding_length": 1024,
                                      "gemma4.embedding_length": 2560,
                                      "gemma4.vision.embedding_length": 768}},
    }
    emb = mc.capabilities_for("qwen3-embedding:4b")
    assert (emb.exists, emb.embedding, emb.completion, emb.embedding_dim, emb.native_context) == \
        (True, True, False, 2560, 40960)
    assert emb.thinks_by_name is False and emb.thinking is False and emb.sends_think_flag is False
    g = mc.capabilities_for("gemma4:e4b")
    assert (g.tools, g.thinking, g.vision, g.native_context, g.architecture) == \
        (True, True, True, 131072, "gemma4")
    assert g.embedding_dim == 2560  # the text model's, not the vision or audio encoder's
    assert g.evidence["tools"] == "api_show" and g.evidence["vision"] == "api_show_capabilities"


def test_an_undescribed_model_is_empty_and_says_why(ollama):
    rec = mc.capabilities_for("not-pulled:latest")
    assert rec.exists is False and not (rec.tools or rec.thinking or rec.embedding)
    assert rec.evidence["tools"] == "ollama_unreachable"


def test_the_profile_reads_the_record(ollama):
    ollama.models = {t: {"capabilities": c, "model_info": {"llama.context_length": 32768}}
                     for t, c in zip(TAGS, itertools.cycle(CAP_SETS))}
    for tag in TAGS:
        rec = mc.capabilities_for(tag, with_vision=False)
        prof = resolver.resolve(tag, "chat")
        assert (prof.supports_tools, prof.supports_thinking, prof.context_window, prof.architecture) == \
            (rec.tools, rec.thinking, rec.native_context, rec.architecture), tag


# ── declared rows ────────────────────────────────────────────────────────────

def test_a_local_row_overrides_api_show(ollama):
    ollama.models = {"mistral:7b": {"capabilities": ["completion", "tools"]}}
    mc.LOCAL_ROWS_PATH.write_text(json.dumps({"mistral:7b": {"tools": False, "native_context": 4096,
                                                              "vision": True}}))
    rec = mc.capabilities_for("mistral:7b")
    assert rec.tools is False and rec.native_context == 4096
    assert rec.evidence["tools"] == "local_row"
    assert rec.vision is False, "vision is not overridable here"
    assert orm.model_supports_tools("mistral:7b") is False


def test_a_shipped_row_applies_and_a_local_row_wins(ollama, monkeypatch):
    ollama.models = {"mistral:7b": {"capabilities": ["completion"]}}
    monkeypatch.setitem(mc.MODEL_CAPABILITY_ROWS, "mistral:7b", {"tools": True})
    assert mc.capabilities_for("mistral:7b").evidence["tools"] == "declared_row"
    assert orm.model_supports_tools("mistral:7b") is True
    mc.LOCAL_ROWS_PATH.write_text(json.dumps({"mistral:7b": {"tools": False}}))
    assert orm.model_supports_tools("mistral:7b") is False


def test_a_broken_local_file_is_ignored(ollama):
    ollama.models = {"mistral:7b": {"capabilities": ["completion", "tools"]}}
    mc.LOCAL_ROWS_PATH.write_text("{not json")
    assert orm.model_supports_tools("mistral:7b") is True


def test_no_rows_ship():
    assert mc.MODEL_CAPABILITY_ROWS == {}


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_flags_models_the_checks_disagree_on(ollama, capsys):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "scripts" / "model_capability_report.py"
    spec = importlib.util.spec_from_file_location("model_capability_report", path)
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)

    ollama.models = {
        # Multimodal, but the name lists that predate /api/show call it blind.
        "qwen3.5:9b": {"capabilities": ["completion", "vision", "tools", "thinking"]},
        # An embedding model with no "embed" in its name: the chat filter
        # now asks Ollama, so the checks agree.
        "bge-m3:latest": {"capabilities": ["embedding"]},
        "llama3.1:8b": {"capabilities": ["completion", "tools"]},
    }
    assert report.main(["--json"]) == 0
    rows = {r["tag"]: r for r in json.loads(capsys.readouterr().out)}
    assert "vision" in rows["qwen3.5:9b"]["disagree"]
    assert rows["qwen3.5:9b"]["vision"]["record"] is True
    assert "text_chat" not in rows["bge-m3:latest"]["disagree"]
    assert rows["bge-m3:latest"]["text_chat"]["is_text_chat_model (name)"] is False
    assert rows["llama3.1:8b"]["disagree"] == []
    assert report.main(["--models", "qwen3.5:9b"]) == 0
    assert "! qwen3.5:9b" in capsys.readouterr().out


# ── reasoning tag pairs ──────────────────────────────────────────────────────

def test_reasoning_tags_default_to_the_shared_list(ollama):
    from backend.services.model_capability_data import INLINE_REASONING_TAGS
    ollama.models = {"qwen3:14b": {"capabilities": ["completion", "thinking"]}}
    rec = mc.capabilities_for("qwen3:14b")
    assert rec.reasoning_tags == INLINE_REASONING_TAGS and rec.evidence["reasoning_tags"] == "default"


def test_a_local_row_sets_a_models_reasoning_tags(ollama):
    ollama.models = {"granite3.2:8b": {"capabilities": ["completion", "thinking"]}}
    mc.LOCAL_ROWS_PATH.write_text(json.dumps({"granite3.2:8b": {
        "reasoning_tags": [["Here is my thought process:", "Here is my response:"]]}}))
    rec = mc.capabilities_for("granite3.2:8b")
    assert rec.reasoning_tags == (("Here is my thought process:", "Here is my response:"),)
    assert rec.evidence["reasoning_tags"] == "local_row"


def test_a_malformed_reasoning_tags_row_is_ignored(ollama):
    from backend.services.model_capability_data import INLINE_REASONING_TAGS
    ollama.models = {"qwen3:14b": {"capabilities": ["completion", "thinking"]}}
    mc.LOCAL_ROWS_PATH.write_text(json.dumps({"qwen3:14b": {"reasoning_tags": "<think>"}}))
    assert mc.capabilities_for("qwen3:14b").reasoning_tags == INLINE_REASONING_TAGS


# ── cleanups the capability report found ─────────────────────────────────────

def test_an_embedding_model_is_not_a_thinker_by_name(ollama):
    assert mc.thinks_by_name("qwen3-embedding:4b-q4_K_M") is False
    assert orm.model_supports_thinking("qwen3-embedding:4b-q4_K_M") is False
    assert mc.thinks_by_name("qwen3:14b") is True
    assert orm.model_supports_thinking("qwen3:14b") is True
    assert "qwen3:14b" not in ollama.shows  # answered by name, no I/O


def test_an_embedding_model_is_not_a_chat_model_whatever_its_name(ollama):
    ollama.models = {
        "bge-m3:latest": {"capabilities": ["embedding"]},
        "llama3.1:8b": {"capabilities": ["completion", "tools"]},
        "moondream:latest": {"capabilities": ["completion", "vision"]},
    }
    assert orm.is_text_chat_model("bge-m3:latest") is False
    assert orm.is_text_chat_model("llama3.1:8b") is True
    assert orm.is_text_chat_model("moondream:latest") is False  # vision-only by name, as before


def test_chat_eligibility_falls_back_to_the_name_rule_with_ollama_down(ollama):
    ollama.down = True
    assert orm.is_text_chat_model("bge-m3:latest") is True
    assert orm.is_text_chat_model("nomic-embed-text:latest") is False


def test_servo_config_asks_ollama_whether_a_model_sees(ollama):
    from backend.services.servo_knowledge_store import get_vision_config
    ollama.models = {
        "qwen3.5:9b": {"capabilities": ["completion", "vision", "tools", "thinking"]},
        "someone/gemma4-26b-text-only:latest": {"capabilities": ["completion", "tools", "thinking"]},
    }
    seeing = get_vision_config("qwen3.5:9b")
    assert seeing["has_vision"] is True and seeing["source"] == "resolver_api_show_capabilities"
    blind = get_vision_config("someone/gemma4-26b-text-only:latest")
    assert blind["has_vision"] is False


def test_embedding_width_comes_from_ollama_before_the_4096_fallback(ollama, monkeypatch):
    import backend.config as config
    from backend.utils.embedding_router import EmbeddingRouter
    ollama.models = {"mxbai-embed-large:latest": {
        "capabilities": ["embedding"], "family": "bert",
        "model_info": {"bert.embedding_length": 1024, "bert.context_length": 512}}}
    monkeypatch.setattr(config, "get_active_embedding_model", lambda: "mxbai-embed-large:latest")
    monkeypatch.setattr(config, "get_embedding_vram_estimates", lambda: {})
    router = object.__new__(EmbeddingRouter)  # not the singleton
    router._embed_dim = None
    router._gpu_embedding = None
    router._cpu_embedding = None
    assert router.embed_dim == 1024
