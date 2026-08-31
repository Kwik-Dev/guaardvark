"""Every Ollama request built outside the chat engine's sized path carries a
num_ctx, so no call runs at whatever the Modelfile baked (4,096 on library
tags, 262,144 on some community ones)."""

from backend.utils import ollama_resource_manager as orm


def test_request_options_fills_num_ctx(monkeypatch):
    monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda name: 8192)
    opts = orm.request_options("gemma4:12b", temperature=0.4)
    assert opts == {"temperature": 0.4, "num_ctx": 8192}


def test_request_options_keeps_an_explicit_window(monkeypatch):
    monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda name: 8192)
    opts = orm.request_options("gemma4:12b", num_predict=1, num_ctx=16384)
    assert opts == {"num_predict": 1, "num_ctx": 16384}


def test_request_options_drops_none_and_resizes(monkeypatch):
    monkeypatch.setattr(orm, "compute_optimal_num_ctx", lambda name: 4096)
    # A caller may pass num_ctx=None (window not known yet); that is "size it".
    opts = orm.request_options("moondream", num_predict=64, num_ctx=None, temperature=None)
    assert opts == {"num_predict": 64, "num_ctx": 4096}
