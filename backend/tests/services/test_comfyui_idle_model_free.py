"""ComfyUI idle model unload.

ComfyUI keeps whatever it just rendered resident. On Apple Silicon there is no
separate VRAM to offload into — ``--disable-smart-memory`` moves weights inside the
SAME unified memory — so a 24 GB Z-Image/FLUX outlives the render, and the RAM side
of admission can then refuse the NEXT job over memory the last one left behind.

These pin the three properties that make the unload safe:

  * it happens on IDLE, never per render — a 10-image batch must unload once, after
    its last image, not reload the weights between every image;
  * it refuses to evict while ComfyUI's queue is non-empty, because /free unloads
    for EVERY client sharing that server (Celery, stills, the Desktop app);
  * a render arms it, on success and on failure alike (a timed-out render has the
    same weights loaded).

Nothing here contacts a real ComfyUI.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

try:
    import backend.services.gpu_resource_policy as grp
except Exception:  # pragma: no cover - import guard mirrors sibling tests
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture(autouse=True)
def _no_real_comfyui(monkeypatch):
    """A stray timer must never POST at a real ComfyUI.

    Only ``free_comfyui_vram`` is stubbed here: the probe tests below exercise the
    REAL ``comfyui_queue_busy``, so stubbing that would make them test the stub.
    The two timer tests that need an idle server patch it themselves.
    """
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **kwargs: True)


# --- the idle timer ---------------------------------------------------------

def test_the_unload_fires_after_the_idle_delay(monkeypatch):
    freed = threading.Event()
    monkeypatch.setattr(grp, "comfyui_queue_busy", lambda **kwargs: False)
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **kwargs: freed.set() or True)

    grp.schedule_free_comfyui_vram(0.02)

    assert freed.wait(5.0), "the idle unload never fired"


def test_each_render_rearms_the_timer_instead_of_freeing_per_image(monkeypatch):
    """A batch must produce ONE unload — this is the whole reason for the delay."""
    calls: list = []
    monkeypatch.setattr(grp, "comfyui_queue_busy", lambda **kwargs: False)
    monkeypatch.setattr(grp, "free_comfyui_vram", lambda **kwargs: calls.append(1) or True)

    grp.schedule_free_comfyui_vram(0.2)   # image 1 of a batch
    time.sleep(0.05)
    grp.schedule_free_comfyui_vram(0.2)   # image 2, before the first timer fires
    time.sleep(0.5)

    assert calls == [1], f"expected exactly one unload, got {len(calls)}"


def test_the_unload_defers_while_the_comfyui_queue_is_not_empty(monkeypatch):
    """Another client's render must not be evicted from under it."""
    rearms: list = []
    monkeypatch.setattr(grp, "comfyui_queue_busy", lambda **kwargs: True)
    monkeypatch.setattr(
        grp, "free_comfyui_vram",
        lambda **kwargs: pytest.fail("must not evict a busy ComfyUI"),
    )
    monkeypatch.setattr(grp, "schedule_free_comfyui_vram", lambda *a, **k: rearms.append(True))

    grp._free_comfyui_when_idle()

    assert rearms == [True], "a deferred unload must re-arm, not give up"


def test_the_unload_frees_nothing_when_comfyui_is_unreachable(monkeypatch):
    monkeypatch.setattr(grp, "comfyui_queue_busy", lambda **kwargs: None)
    monkeypatch.setattr(
        grp, "free_comfyui_vram", lambda **kwargs: pytest.fail("nothing to unload")
    )

    grp._free_comfyui_when_idle()   # must simply return


def test_delay_zero_disables_the_unload(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_MODEL_FREE_DELAY_S", "0")
    monkeypatch.setattr(
        grp, "free_comfyui_vram", lambda **kwargs: pytest.fail("disabled by config")
    )

    grp.schedule_free_comfyui_vram()
    time.sleep(0.05)


def test_an_invalid_delay_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_COMFYUI_MODEL_FREE_DELAY_S", "soon")

    assert grp.comfyui_model_free_delay_s() == grp._COMFY_FREE_DELAY_S


# --- the safety probe -------------------------------------------------------

def test_the_queue_probe_reads_running_and_pending(monkeypatch):
    import requests

    def _get(url, timeout=None):
        assert url.endswith("/queue")
        return SimpleNamespace(json=lambda: {"queue_running": [[0, "x"]], "queue_pending": []})

    monkeypatch.setattr(requests, "get", _get)

    assert grp.comfyui_queue_busy() is True


def test_the_queue_probe_reports_idle_for_an_empty_queue(monkeypatch):
    import requests

    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None: SimpleNamespace(
            json=lambda: {"queue_running": [], "queue_pending": []}
        ),
    )

    assert grp.comfyui_queue_busy() is False


def test_the_queue_probe_treats_an_error_as_unknown(monkeypatch):
    import requests

    def _boom(*args, **kwargs):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(requests, "get", _boom)

    assert grp.comfyui_queue_busy() is None


# --- the render call site ---------------------------------------------------

def _stub_generator(monkeypatch, *, wait_result="images", fetch_ok=True):
    """A ComfyUIImageGenerator whose render path never touches the network."""
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator

    gen = ComfyUIImageGenerator(comfy_url="http://127.0.0.1:1")
    monkeypatch.setattr(gen, "_available", lambda: True)
    monkeypatch.setattr(gen, "_preflight_loras", lambda paths: None)
    monkeypatch.setattr(
        gen, "_build_workflow",
        lambda **kwargs: {"sampler": {"class_type": "KSampler", "inputs": {"steps": 9}}},
    )
    monkeypatch.setattr(gen, "_queue", lambda workflow: "pid-1")
    monkeypatch.setattr(gen, "_wait", lambda prompt_id: wait_result)
    monkeypatch.setattr(
        gen, "_fetch_first_image", lambda outputs, path: path if fetch_ok else None
    )
    return gen


@pytest.mark.parametrize("fetch_ok", [True, False])
def test_generate_image_arms_the_idle_unload(monkeypatch, fetch_ok):
    armed: list = []
    monkeypatch.setattr(grp, "schedule_free_comfyui_vram", lambda *a, **k: armed.append(True))
    gen = _stub_generator(monkeypatch, fetch_ok=fetch_ok)

    if fetch_ok:
        assert gen.generate_image(prompt="a lighthouse", output_path="/tmp/x.png") == "/tmp/x.png"
    else:
        with pytest.raises(RuntimeError, match="produced no image"):
            gen.generate_image(prompt="a lighthouse", output_path="/tmp/x.png")

    assert armed == [True]


def test_generate_image_arms_the_idle_unload_when_the_render_times_out(monkeypatch):
    """A timed-out render has the same weights loaded, so it must still arm."""
    armed: list = []
    monkeypatch.setattr(grp, "schedule_free_comfyui_vram", lambda *a, **k: armed.append(True))
    gen = _stub_generator(monkeypatch, wait_result=None)

    with pytest.raises(RuntimeError, match="timed out"):
        gen.generate_image(prompt="a lighthouse", output_path="/tmp/x.png")

    assert armed == [True]
