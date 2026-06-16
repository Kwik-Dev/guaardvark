"""P1 — the music-video Director (the storyboard layer).

Locks: distinct per-cut prompts when the LLM behaves, the global style appended as a
suffix, and (when director_enabled) *energy-cued variations of the style* on LLM failure
instead of N verbatim repeats of the global style. The verbatim "all global style"
fallback is disabled for the music video feature (per user request); diagnostics are
surfaced so the UI can warn the user.
"""
import json

import pytest

try:
    import backend.services.music_video_director as director
    from backend.services.music_video_director import generate_scene_prompts, _parse_prompts
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


class _OllamaModelObj:
    """Mimics the real ollama-lib ListResponse.Model (tag under .model, NOT ['name'])
    — the exact shape that caused the silent-fallback bug."""
    def __init__(self, model): self.model = model


@pytest.fixture(autouse=True)
def _stub_model_list(monkeypatch):
    # Keep _resolve_model hermetic — never hit the real ollama daemon in unit tests.
    import ollama
    monkeypatch.setattr(ollama, "list", lambda: {"models": [_OllamaModelObj("gemma4:e4b")]})


def _plan(n):
    return [
        {"index": i, "start_s": float(i), "end_s": float(i) + 1.0,
         "energy": 0.1 * i, "section_label": "drop" if i % 2 else "intro"}
        for i in range(n)
    ]


def _fake_chat(shots):
    def chat(*, model, messages, format=None, options=None):
        return {"message": {"content": json.dumps({"shots": shots})}}
    return chat


def test_distinct_prompts_with_style_suffix(monkeypatch):
    import ollama
    monkeypatch.setattr(ollama, "chat", _fake_chat([
        {"index": 0, "prompt": "a lone crow on a wire at dawn, wide"},
        {"index": 1, "prompt": "crows bursting from a rooftop, fast tracking"},
        {"index": 2, "prompt": "a single feather drifting down, close-up"},
    ]))
    out = generate_scene_prompts("ink-wash animation, deep blue", _plan(3))
    assert len(out) == 3
    assert out[0] != out[1] != out[2]                       # distinct, not N reseeds
    assert all(p.endswith("ink-wash animation, deep blue") for p in out)  # style suffix kept
    assert "lone crow" in out[0] and "bursting" in out[1]


def test_fallback_when_llm_raises(monkeypatch):
    import ollama
    def boom(**kw):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ollama, "chat", boom)
    out = generate_scene_prompts("STYLE", _plan(4))
    # Fallback now disabled for music-video: we still get energy-cued variations instead of
    # N identical repeats of the global style. Guard ensures distinctness + suffix.
    assert len(out) == 4
    assert all(p.endswith("STYLE") for p in out)
    assert len(set(out)) > 1  # cued variations make them non-identical
    # At least some should carry an injected cue (from the low/high energy cues)
    assert any(any(kw in p.lower() for kw in ("tighter", "dynamic", "framing", "slow", "atmospheric", "diffuse")) for p in out)


def test_fallback_when_garbage(monkeypatch):
    import ollama
    monkeypatch.setattr(ollama, "chat", lambda **kw: {"message": {"content": "not json at all"}})
    out = generate_scene_prompts("STYLE", _plan(2))
    # Plain identical global fallback disabled: garbage -> energy cued variations (n=2 threshold
    # means needs_fix triggers for 1 unique).
    assert len(out) == 2
    assert all(p.endswith("STYLE") for p in out)
    # They should be distinct via cue injection (or at minimum not both exactly "STYLE").
    assert out[0] != out[1] or "STYLE" not in out[0]  # relaxed for tiny n edge


def test_missing_cut_falls_back_per_index(monkeypatch):
    # Model returns a prompt for cut 0 but not cut 1 → cut 1 uses the global style.
    import ollama
    monkeypatch.setattr(ollama, "chat", _fake_chat([{"index": 0, "prompt": "rain on glass, macro"}]))
    out = generate_scene_prompts("STYLE", _plan(2))
    assert out[0] == "rain on glass, macro, STYLE"
    assert out[1] == "STYLE"


def test_empty_plan_returns_empty():
    assert generate_scene_prompts("STYLE", []) == []


def test_parse_tolerates_fences_and_prose():
    content = 'Here you go:\n```json\n{"shots": [{"index": 0, "prompt": "p0"}]}\n```'
    assert _parse_prompts(content, 1) == {0: "p0"}


def test_parse_accepts_bare_list():
    content = '[{"index": 0, "prompt": "a"}, {"index": 1, "prompt": "b"}]'
    assert _parse_prompts(content, 2) == {0: "a", 1: "b"}


def test_resolve_model_prefers_available_gemma(monkeypatch):
    import ollama
    # Real ollama shape (.model objects): preferred tag isn't pulled (only
    # gemma4:latest is) → resolve to the pulled gemma. This is the bug that made the
    # default model silently fall back to no-storyboard.
    monkeypatch.setattr(ollama, "list",
                        lambda: {"models": [_OllamaModelObj("gemma4:latest"), _OllamaModelObj("llama3:latest")]})
    assert director._resolve_model("gemma4:e4b") == "gemma4:latest"
    # Preferred IS pulled → keep it.
    monkeypatch.setattr(ollama, "list", lambda: {"models": [_OllamaModelObj("gemma4:e4b")]})
    assert director._resolve_model("gemma4:e4b") == "gemma4:e4b"
    # Legacy dict shape ({"name": ...}) still handled.
    monkeypatch.setattr(ollama, "list", lambda: {"models": [{"name": "gemma4:legacy"}]})
    assert director._resolve_model("gemma4:e4b") == "gemma4:legacy"
