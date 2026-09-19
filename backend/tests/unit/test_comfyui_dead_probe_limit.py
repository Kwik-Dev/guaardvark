"""A queued prompt survives DEAD_PROBE_LIMIT missed liveness probes, not five.

Large model loads (the 22B LTX transformers on a 16 GB card) stall ComfyUI's
HTTP server for well over 20 s; a limit of five declared them orphaned.
"""

from types import SimpleNamespace

from backend.services import comfyui_video_generator as cvg
from backend.services.comfyui_video_generator import DEAD_PROBE_LIMIT, ComfyUIVideoGenerator


def test_limit_is_thirty_and_the_wait_loop_uses_it(monkeypatch):
    assert DEAD_PROBE_LIMIT == 30
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    probes = []
    gen._comfyui_alive = lambda: probes.append(1) or False
    gen._gpu_activity_snapshot = lambda: {}
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(cvg, "time", SimpleNamespace(
        time=lambda: clock.now, sleep=lambda s: setattr(clock, "now", clock.now + s),
    ))

    assert gen._wait_for_completion("prompt-x", timeout=600) is None
    assert len(probes) == DEAD_PROBE_LIMIT
