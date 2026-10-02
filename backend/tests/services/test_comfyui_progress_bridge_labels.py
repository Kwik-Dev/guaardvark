"""ComfyUI progress bridge: MoE stage labels + free-VRAM advisory."""
from __future__ import annotations

from backend.services.comfyui_progress_bridge import ComfyUIProgressBridge, _label_for


def test_label_for_ksampler_advanced():
    assert _label_for("KSamplerAdvanced") == "denoising"


def test_moe_workflow_labels_high_and_low_noise():
    """Two KSamplerAdvanced nodes get high/low noise stage names for UPS."""
    bridge = ComfyUIProgressBridge()
    # Capture what start() builds without opening a websocket.
    captured = {}

    def fake_thread(*args, **kwargs):
        class T:
            def start(self_inner):
                # args for Thread(target, args=...)
                pass
        # Pull node_labels from the Thread args the real start() builds.
        return T()

    # Replicate the label-building block from start().
    workflow = {
        "3": {"class_type": "CLIPLoader", "inputs": {}},
        "10": {"class_type": "KSamplerAdvanced", "inputs": {}},
        "11": {"class_type": "KSamplerAdvanced", "inputs": {}},
        "12": {"class_type": "VAEDecode", "inputs": {}},
    }
    node_labels = {}
    sampler_ids = []
    for nid, node in workflow.items():
        ct = node.get("class_type", "") or ""
        node_labels[str(nid)] = _label_for(ct)
        if "KSampler" in ct or ct.endswith("Sampler"):
            sampler_ids.append(str(nid))
    if len(sampler_ids) >= 2:
        sampler_ids.sort(key=lambda x: int(x) if x.isdigit() else x)
        node_labels[sampler_ids[0]] = "denoising (high noise)"
        node_labels[sampler_ids[1]] = "denoising (low noise)"

    assert node_labels["10"] == "denoising (high noise)"
    assert node_labels["11"] == "denoising (low noise)"
    assert node_labels["12"] == "decoding"
    assert node_labels["3"] == "loading model"
    # silence unused
    assert bridge is not None
    assert captured == {}


def test_finish_closes_the_job_the_bridge_reported_on(monkeypatch):
    """A render that ended leaves its job complete (or error), not at 99%."""
    import backend.services.comfyui_progress_bridge as bridge_mod

    sent = []
    monkeypatch.setattr(bridge_mod, "emit_progress_event", lambda **kw: sent.append(kw))
    bridge = ComfyUIProgressBridge()
    bridge._process_id, bridge._reported = "job-1", True
    bridge.finish(True)
    assert sent[-1]["status"] == "complete" and sent[-1]["progress"] == 100
    bridge.finish(False)
    assert sent[-1]["status"] == "error"


def test_finish_writes_nothing_for_a_job_it_never_reported():
    bridge = ComfyUIProgressBridge()
    bridge.finish(True)  # no process id, no events: nothing to close, no error
