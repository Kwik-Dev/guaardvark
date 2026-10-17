"""A Celery worker reranks with the backend's GPU model, else on its own CPU.

Workers cannot see the GPU, and on the CPU the model takes tens of seconds per
retrieval. The backend scores for them (POST /api/meta/reranker/score) through
the code chat scores with, so a worker's scores are chat's. No backend, GPU or
model here: request_json and the CrossEncoder are replaced.
"""

import logging
import math

import numpy as np
import pytest
from flask import Flask

from backend.utils import reranker
from backend.utils.backend_http import BackendError, BackendResponse


@pytest.fixture(autouse=True)
def _clean_reranker_state(monkeypatch):
    saved = (reranker._model, reranker._model_device, reranker._model_vram_mb,
             reranker._inflight, reranker._load_failed_reason)
    monkeypatch.setattr(reranker, "_remote",
                        {"retry_at": 0.0, "reason": None, "noted": set(), "working": False})
    monkeypatch.setenv("GUAARDVARK_RERANK_CROSS_ENCODER", "true")
    monkeypatch.delenv("GUAARDVARK_RERANK_MODEL", raising=False)
    monkeypatch.delenv("GUAARDVARK_MCP_PROCESS", raising=False)
    yield
    (reranker._model, reranker._model_device, reranker._model_vram_mb,
     reranker._inflight, reranker._load_failed_reason) = saved


class _FakeCrossEncoder:
    """Deterministic scores with many significant digits, float32 like the real one."""

    def __init__(self):
        self.calls = []

    def predict(self, pairs, show_progress_bar=False):
        self.calls.append(list(pairs))
        return np.array([math.sin(len(q) + 7 * len(t)) for q, t in pairs], dtype=np.float32)


@pytest.fixture
def worker(monkeypatch):
    """A Celery worker, which cannot see the GPU."""
    monkeypatch.setenv("CELERY_WORKER_MODE", "true")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(reranker, "_clock", lambda: now[0])
    return now


def _backend_answers(monkeypatch, data):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        if isinstance(data, Exception):
            raise data
        return BackendResponse(status=200, body=data, data=data)

    monkeypatch.setattr("backend.utils.backend_http.request_json", fake_request)
    return calls


def _local_model(monkeypatch, device="cpu"):
    model = _FakeCrossEncoder()
    monkeypatch.setattr(reranker, "_get_model", lambda: model)
    monkeypatch.setattr(reranker, "_model_device", device)
    return model


def _docs():
    return [{"text": "a short one"}, {"text": "x" * 5000}, {"text": "mid length text"}]


# --- Who asks the backend ---------------------------------------------------------

def test_only_a_worker_without_the_gpu_asks_the_backend(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
    # The backend on a box without a usable GPU hides CUDA too; it must not ask itself.
    assert reranker._worker_without_gpu() is None
    monkeypatch.setenv("CELERY_WORKER_MODE", "true")
    assert reranker._worker_without_gpu() == "CUDA_VISIBLE_DEVICES is empty"
    monkeypatch.setattr(reranker, "_cuda_unusable_reason", lambda: None)
    assert reranker._worker_without_gpu() is None, "a worker that sees the card uses it"


def test_the_backend_process_scores_itself(monkeypatch):
    monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
    calls = _backend_answers(monkeypatch, {"applied": True, "scores": [1.0, 2.0, 3.0]})
    _local_model(monkeypatch, device="cuda")
    _, info = reranker.rerank("q", _docs())
    assert calls == []
    assert info["applied"] is True and "via" not in info


# --- The worker side ------------------------------------------------------------------

def test_a_worker_takes_the_backends_scores_and_loads_nothing(monkeypatch, worker):
    calls = _backend_answers(monkeypatch, {
        "applied": True, "scores": [0.2, 0.9, 0.5], "model": reranker.model_name(), "device": "cuda",
    })
    monkeypatch.setattr(reranker, "_get_model",
                        lambda: pytest.fail("the worker loaded a model although the backend scored"))

    out, info = reranker.rerank("q", _docs())

    assert [r["rerank_score"] for r in out] == [0.9, 0.5, 0.2]
    assert out[0]["text"] == "x" * 5000
    assert info == {"applied": True, "reason": None, "model": reranker.model_name(),
                    "device": "cuda", "via": "backend", "scored": 3}
    (call,) = calls
    assert call["method"] == "POST" and call["path"] == reranker.SCORE_PATH
    assert call["own_backend"] is True, "only this machine's own backend"
    assert call["payload"] == {
        "query": "q",
        "passages": ["a short one", "x" * reranker.PASSAGE_CHARS, "mid length text"],
        "model": reranker.model_name(),
    }
    assert call["connect_timeout"] == reranker.REMOTE_CONNECT_TIMEOUT_S
    assert call["read_timeout"] == reranker.REMOTE_READ_TIMEOUT_S


def test_a_backend_that_does_not_answer_means_the_cpu_and_one_warning(monkeypatch, worker, clock, caplog):
    calls = _backend_answers(monkeypatch, BackendError(
        "unreachable", "The Guaardvark backend is not answering at http://127.0.0.1:5000."))
    model = _local_model(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=reranker.logger.name):
        out, info = reranker.rerank("q", _docs())
        reranker.rerank("q", _docs())
        assert len(calls) == 1, "within the retry window the backend is not asked again"
        clock[0] += reranker.REMOTE_RETRY_S + 1
        reranker.rerank("q", _docs())

    assert len(calls) == 2
    assert len(model.calls) == 3
    assert info["applied"] is True and info["device"] == "cpu" and "via" not in info
    assert info["backend_unavailable"].startswith("the backend did not answer (unreachable")
    warnings = [r.getMessage() for r in caplog.records
                if r.name == reranker.logger.name and r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    assert "reranking on this worker's CPU" in warnings[0]


def test_a_backend_without_room_on_its_card_is_asked_again_later(monkeypatch, worker, clock, caplog):
    refusal = {"applied": False, "reason": "1200 MB free < 3000 MB floor"}
    calls = _backend_answers(monkeypatch, refusal)
    _local_model(monkeypatch)

    with caplog.at_level(logging.INFO, logger=reranker.logger.name):
        _, info = reranker.rerank("q", _docs())
    assert info["device"] == "cpu"
    assert info["backend_unavailable"] == "the backend could not score on its GPU (1200 MB free < 3000 MB floor)"
    ours = [r for r in caplog.records if r.name == reranker.logger.name]
    assert not [r for r in ours if r.levelno >= logging.WARNING], "a refusal is expected, not a fault"
    assert any("could not score on its GPU" in r.getMessage() for r in ours)

    refusal.update({"applied": True, "scores": [0.1, 0.3, 0.2], "device": "cuda",
                    "model": reranker.model_name(), "reason": None})
    clock[0] += reranker.REMOTE_RETRY_S + 1
    _, info = reranker.rerank("q", _docs())
    assert len(calls) == 2
    assert info["via"] == "backend" and info["device"] == "cuda"


@pytest.mark.parametrize("scores", [[0.1, 0.2], [0.1, float("nan"), 0.3], None, ["0.1", "0.2", "0.3"]])
def test_an_answer_without_one_usable_score_per_passage_is_not_used(monkeypatch, worker, scores):
    _backend_answers(monkeypatch, {"applied": True, "scores": scores})
    model = _local_model(monkeypatch)
    out, info = reranker.rerank("q", _docs())
    assert len(model.calls) == 1
    assert info["device"] == "cpu" and "via" not in info
    assert all(isinstance(r["rerank_score"], float) for r in out)


# --- The backend side ---------------------------------------------------------------

def test_the_backend_scores_only_on_the_gpu(monkeypatch):
    reranker._model = _FakeCrossEncoder()
    reranker._model_device = "cpu"
    reranker._load_failed_reason = None
    info = reranker.score_on_gpu("q", ["a", "b"])
    assert info["applied"] is False
    assert info["reason"] == "the reranker is loaded on the cpu, not the GPU"
    assert reranker._model.calls == []


def test_a_card_without_room_is_refused_without_a_cpu_load(monkeypatch):
    reranker._model = None
    reranker._load_failed_reason = None
    monkeypatch.setattr(reranker, "is_installed", lambda: True)
    monkeypatch.setattr(reranker, "_why_not_cuda", lambda: "1200 MB free < 3000 MB floor")
    monkeypatch.setattr(reranker, "_load_locked", lambda device: pytest.fail(f"loaded on {device}"))
    info = reranker.score_on_gpu("q", ["a", "b"])
    assert info["applied"] is False and info["reason"] == "1200 MB free < 3000 MB floor"


def test_a_card_with_room_takes_the_model_and_scores(monkeypatch):
    reranker._model = None
    reranker._load_failed_reason = None
    loads = []

    def fake_load(device):
        loads.append(device)
        reranker._model = _FakeCrossEncoder()
        reranker._model_device = device

    monkeypatch.setattr(reranker, "is_installed", lambda: True)
    monkeypatch.setattr(reranker, "_why_not_cuda", lambda: None)
    monkeypatch.setattr(reranker, "_load_locked", fake_load)

    info = reranker.score_on_gpu("q", ["a", "bbbb"])

    assert loads == ["cuda"]
    assert info["applied"] is True and info["device"] == "cuda" and info["scored"] == 2
    assert info["scores"] == [float(np.float32(math.sin(1 + 7))), float(np.float32(math.sin(1 + 28)))]
    assert reranker._inflight == 0


def test_another_model_or_a_switched_off_reranker_is_refused(monkeypatch):
    info = reranker.score_on_gpu("q", ["a"], model="someone/else")
    assert info["applied"] is False and info["reason"].endswith("not someone/else")
    monkeypatch.setenv("GUAARDVARK_RERANK_CROSS_ENCODER", "false")
    info = reranker.score_on_gpu("q", ["a"], model=reranker.model_name())
    assert info["applied"] is False and info["reason"].startswith("disabled")


# --- The route, and the proof that a worker gets chat's scores ----------------------

@pytest.fixture
def client():
    from backend.api.index_mgmt_api import index_mgmt_bp

    app = Flask(__name__)
    app.register_blueprint(index_mgmt_bp)
    return app.test_client()


def test_a_worker_gets_exactly_the_scores_chat_gets(monkeypatch, client):
    """Same model, same inputs: through the route the scores match chat's to the bit."""
    model = _FakeCrossEncoder()
    reranker._model = model
    reranker._model_device = "cuda"
    reranker._load_failed_reason = None
    query = "which folders does the nightly backup cover?"

    monkeypatch.delenv("CELERY_WORKER_MODE", raising=False)
    chat_out, chat_info = reranker.rerank(query, _docs())

    def through_the_route(method, path, payload=None, **kwargs):
        resp = client.open(path, method=method, json=payload)
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        return BackendResponse(status=200, body=body, data=body)

    monkeypatch.setattr("backend.utils.backend_http.request_json", through_the_route)
    monkeypatch.setenv("CELERY_WORKER_MODE", "true")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    worker_out, worker_info = reranker.rerank(query, _docs())

    assert chat_info.get("via") is None and worker_info["via"] == "backend"
    assert [(r["text"], r["rerank_score"]) for r in worker_out] == \
        [(r["text"], r["rerank_score"]) for r in chat_out]
    assert model.calls[0] == model.calls[1], "the model saw the same pairs"


@pytest.mark.parametrize("kwargs", [
    {"data": "q", "content_type": "text/plain"},
    {"json": ["q", ["a"]]},
    {"json": {"passages": ["a"]}},
    {"json": {"query": "q", "passages": "a"}},
    {"json": {"query": "q", "passages": ["a", 2]}},
    {"json": {"query": "q", "passages": ["a"] * (reranker.MAX_REMOTE_PASSAGES + 1)}},
    {"json": {"query": "q" * (reranker.MAX_REMOTE_QUERY_CHARS + 1), "passages": ["a"]}},
    {"json": {"query": "q", "passages": ["a"], "model": 3}},
])
def test_the_route_refuses_a_malformed_body(monkeypatch, client, kwargs):
    monkeypatch.setattr(reranker, "score_on_gpu", lambda *a, **k: pytest.fail("scored a malformed body"))
    resp = client.post(reranker.SCORE_PATH, **kwargs)
    assert resp.status_code == 400
    assert resp.get_json()["error"]


def test_the_route_answers_a_refusal_as_data(monkeypatch, client):
    seen = {}

    def fake_score(query, passages, model=None):
        seen.update(query=query, passages=passages, model=model)
        return {"applied": False, "reason": "no usable GPU in this process (CUDA_VISIBLE_DEVICES is empty)"}

    monkeypatch.setattr(reranker, "score_on_gpu", fake_score)
    resp = client.post(reranker.SCORE_PATH, json={"query": "q", "passages": ["a", "b"], "model": "m"})
    assert resp.status_code == 200
    assert resp.get_json()["applied"] is False
    assert seen == {"query": "q", "passages": ["a", "b"], "model": "m"}


def test_the_route_needs_this_machine_or_the_key():
    from backend.utils.auth_guard import _is_protected

    app = Flask(__name__)
    with app.test_request_context(reranker.SCORE_PATH, method="POST"):
        assert _is_protected() is True
