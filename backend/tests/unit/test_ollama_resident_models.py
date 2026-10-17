"""resident_entry says whether a model is loaded on the GPU and staying loaded.

The /api/ps payloads have the shapes Ollama 0.40 returns, including a
``llamacpp:<digest>`` model listed under ``sha256-<digest>:latest``. Digests
are made up.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import ollama_resource_manager as orm

LLAMACPP = "2cf98652fdc6" + "a" * 52
GGML = "da406dc37ca0" + "b" * 52
G12B = "4eb23ef187e2" + "c" * 52

TAGS = [
    {"name": "gemma4:e2b", "model": "gemma4:e2b", "digest": LLAMACPP},
    {"name": "gemma4:e2b", "model": "gemma4:e2b", "digest": GGML},
    {"name": f"llamacpp:{LLAMACPP}", "model": f"llamacpp:{LLAMACPP}", "digest": LLAMACPP},
    {"name": "gemma4:12b", "model": "gemma4:12b", "digest": G12B},
]


def _expires(seconds: float) -> str:
    when = datetime.now(timezone(timedelta(hours=-4))) + timedelta(seconds=seconds)
    # Ollama writes nanoseconds: 2026-10-16T22:32:17.138656931-04:00
    return re.sub(r"(\.\d{6})", r"\g<1>931", when.isoformat(timespec="microseconds"))


def _ps_entry(name, digest, *, vram=1_900_000_000, expires_in=600.0, ctx=32768):
    return {
        "name": name, "model": name, "digest": digest, "size": 2_000_000_000, "size_vram": vram,
        "expires_at": _expires(expires_in), "context_length": ctx,
    }


class _Resp:
    ok = True

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def ps(monkeypatch):
    """Serve /api/ps from ``ps.models`` and count the calls."""
    state = {"models": [], "calls": 0}

    def get(url, timeout=None):
        assert url.endswith("/api/ps")
        state["calls"] += 1
        return _Resp({"models": list(state["models"])})

    orm.clear_cache()
    monkeypatch.setattr(orm.requests, "get", get)
    monkeypatch.setattr(orm, "_ollama_tags", lambda: TAGS)
    yield state
    orm.clear_cache()


def test_an_exact_name_is_resident(ps):
    ps["models"] = [_ps_entry("gemma4:12b", G12B)]
    entry = orm.resident_entry("gemma4:12b")
    assert entry is not None and entry["context_length"] == 32768


def test_another_size_of_the_family_is_not_the_model(ps):
    ps["models"] = [_ps_entry("gemma4:e2b", GGML)]
    assert orm.resident_entry("gemma4:12b") is None


def test_a_llamacpp_alias_listed_under_its_digest_is_resident(ps):
    ps["models"] = [_ps_entry(f"sha256-{LLAMACPP}:latest", LLAMACPP)]
    entry = orm.resident_entry(f"llamacpp:{LLAMACPP}")
    assert entry is not None and entry["digest"] == LLAMACPP


def test_a_name_with_two_builds_matches_by_name_only(ps):
    """gemma4:e2b is listed under two digests: a request by that name could
    load the build that is not resident, so the digest alone does not count."""
    ps["models"] = [_ps_entry(f"sha256-{LLAMACPP}:latest", LLAMACPP)]
    assert orm.resident_entry("gemma4:e2b") is None
    ps["models"] = [_ps_entry("gemma4:e2b", LLAMACPP)]
    orm.clear_cache()
    assert orm.resident_entry("gemma4:e2b") is not None


def test_a_model_off_the_gpu_is_not_resident(ps):
    ps["models"] = [_ps_entry("gemma4:12b", G12B, vram=0)]
    assert orm.resident_entry("gemma4:12b") is None


def test_a_model_about_to_be_dropped_is_not_resident(ps):
    ps["models"] = [_ps_entry("gemma4:12b", G12B, expires_in=3.0)]
    assert orm.resident_entry("gemma4:12b") is None


def test_a_pinned_model_is_resident(ps):
    ps["models"] = [_ps_entry("gemma4:12b", G12B, expires_in=300 * 365 * 86400.0)]
    assert orm.resident_entry("gemma4:12b") is not None


def test_ps_is_read_once_within_the_ttl(ps):
    ps["models"] = [_ps_entry("gemma4:12b", G12B)]
    orm.resident_entry("gemma4:12b")
    orm.resident_entry("gemma4:12b")
    orm.resident_models()
    assert ps["calls"] == 1


def test_ps_is_read_again_after_the_ttl(ps):
    orm.resident_models(ttl_s=0.0)
    orm.resident_models(ttl_s=0.0)
    assert ps["calls"] == 2


def test_an_unreachable_ollama_has_nothing_resident(monkeypatch):
    def down(url, timeout=None):
        raise ConnectionError("refused")

    orm.clear_cache()
    monkeypatch.setattr(orm.requests, "get", down)
    try:
        assert orm.resident_models() == []
        assert orm.resident_entry("gemma4:12b") is None
    finally:
        orm.clear_cache()
