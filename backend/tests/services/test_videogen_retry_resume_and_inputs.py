"""Video generation: retry data, restart resume, request parsing, legacy
lora_name, FreeU removal and the MiniMax reference id ranges.

No ComfyUI, GPU or worker thread: the generator is built with __new__ and the
queue is a plain list."""
import queue
import threading
from types import SimpleNamespace

import pytest

from backend.services import batch_video_generator as bvg
from backend.services import video_model_registry as vmr
from backend.services.batch_video_generator import (
    BatchVideoGenerator,
    BatchVideoItem,
    BatchVideoRequest,
    BatchVideoResult,
)
from backend.services.comfyui_video_generator import ComfyUIVideoGenerator


def _generator(tmp_path, monkeypatch):
    gen = BatchVideoGenerator.__new__(BatchVideoGenerator)
    gen.base_output_dir = tmp_path
    gen.batch_lock = threading.Lock()
    gen.active_batches = {}
    gen.cancel_events = {}
    gen.queue_order = []
    gen.batch_queue = queue.Queue()
    monkeypatch.setattr(gen, "_save_metadata", lambda status: None, raising=False)
    return gen


# ── retry data ───────────────────────────────────────────────────────────────

def test_retry_data_keeps_the_storyboard_concept_and_director_guidance(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch)
    status = gen.start_batch_from_prompts(
        prompts=["a heist"] * 3, batch_id="VideoBatch_t1", model="wan22-5b",
        storyboard_concept="a heist", director_guidance="noir")
    params = status.retry_data["params"]
    assert params["storyboard_concept"] == "a heist"
    assert params["director_guidance"] == "noir"
    assert "freeu" not in params


def test_planned_shots_replace_the_placeholders_in_retry_data(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch)
    status = gen.start_batch_from_prompts(
        prompts=["a heist"] * 2, batch_id="VideoBatch_t2", model="wan22-5b", storyboard_concept="a heist")
    request, _ = gen.batch_queue.get_nowait()
    request.items[0].prompt, request.items[1].prompt = "shot one", "shot two"
    gen._record_storyboard_shots(status, request)
    assert status.retry_data["prompts"] == ["shot one", "shot two"]
    assert status.retry_data["params"]["storyboard_concept"] is None
    assert status.retry_data["params"]["enhance_prompt"] is False


# ── resume after a restart ───────────────────────────────────────────────────

def test_resume_keeps_finished_clips_and_renders_only_the_rest(tmp_path, monkeypatch):
    gen = _generator(tmp_path, monkeypatch)
    batch_dir = tmp_path / "VideoBatch_t3"
    batch_dir.mkdir()
    (batch_dir / "a.mp4").write_bytes(b"x")
    saved = [{"item_id": "A", "success": True, "video_path": "a.mp4"},
             {"item_id": "B", "success": True, "video_path": "gone.mp4"}]
    carried = bvg._finished_results(saved, ["A", "B", "C"], batch_dir)
    assert [r.item_id for r in carried] == ["A"]

    status = gen.start_batch_from_prompts(
        prompts=["p1", "p2", "p3"], batch_id="VideoBatch_t3", model="wan22-5b",
        item_ids=["A", "B", "C"], carried_results=carried)
    request, _ = gen.batch_queue.get_nowait()
    assert [i.id for i in request.items] == ["B", "C"]
    assert status.total_videos == 3 and status.completed_videos == 1
    assert [r.item_id for r in status.results] == ["A"]
    # Retry and re-render still see the whole batch.
    assert status.retry_data["item_ids"] == ["A", "B", "C"]


def test_a_new_batch_never_reuses_mismatched_item_ids():
    assert bvg._reused_item_ids(["A", "B"], 2) == ["A", "B"]
    fresh = bvg._reused_item_ids(["A"], 2)
    assert len(fresh) == 2 and "A" not in fresh
    assert len(set(bvg._reused_item_ids(["A", "A"], 2))) == 2


def test_storyboard_reports_whether_it_replaced_the_placeholders(monkeypatch):
    from backend.services import director_service
    shots = SimpleNamespace(shots=[SimpleNamespace(prompt="s1"), SimpleNamespace(prompt="s2")])
    monkeypatch.setattr(director_service, "plan", lambda brief: shots)
    req = BatchVideoRequest(batch_id="b", items=[BatchVideoItem(id="1", prompt="c"), BatchVideoItem(id="2", prompt="c")],
                            output_dir="/tmp/x", storyboard_concept="c")
    assert BatchVideoGenerator._apply_storyboard(SimpleNamespace(), req) is True
    assert BatchVideoGenerator._apply_storyboard(
        SimpleNamespace(), BatchVideoRequest(batch_id="b", items=[], output_dir="/tmp/x")) is False


# ── request parsing ──────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from flask import Flask
    from backend.api.batch_video_generation_api import batch_video_bp
    app = Flask(__name__)
    app.register_blueprint(batch_video_bp)
    return app.test_client()


@pytest.mark.parametrize("field,value", [
    ("motion_strength", "fast"), ("lora_strength", "x"), ("frames_per_batch", "two"),
    ("interpolation_multiplier", "2x"), ("guidance_scale", "high"), ("storyboard_shots", "many"),
    ("teacache_threshold", "on"), ("subject_ids", ["bob"]),
])
def test_a_field_that_is_not_a_number_is_a_400(client, monkeypatch, field, value):
    from backend.api import batch_video_generation_api as api
    monkeypatch.setattr(api, "_resolve_request_model", lambda data, role: ("wan22-5b", None))
    monkeypatch.setattr(api, "prepare_video_model", lambda mid: (True, None))
    monkeypatch.setattr(api, "_last_frames", lambda data, mid: ([], None))
    resp = client.post("/api/batch-video/generate/text", json={"prompts": ["a cat"], field: value})
    assert resp.status_code == 400, resp.get_json()


def test_zero_tuning_values_mean_off():
    from backend.api.batch_video_generation_api import _render_params
    meta = _render_params({"teacache_threshold": 0, "feta_weight": "0"}, "wan22-5b")["metadata"]
    assert meta["teacache_threshold"] is None and meta["feta_weight"] is None


def test_freeu_is_accepted_and_ignored():
    from backend.api.batch_video_generation_api import _render_params
    assert "freeu" not in _render_params({"freeu": "true"}, "wan22-5b")


def test_model_download_without_json_is_a_400(client):
    resp = client.post("/api/batch-video/models/download", data="model_id=x")
    assert resp.status_code == 400


def test_retry_runs_the_new_request_preflight(client, monkeypatch):
    from backend.api import batch_video_generation_api as api
    orig = SimpleNamespace(retry_data={"mode": "text", "prompts": ["a"], "params": {"model": "wan22-5b"}})
    started = []
    fake = SimpleNamespace(get_batch_status=lambda bid: orig,
                           start_batch_from_prompts=lambda **kw: started.append(kw))
    monkeypatch.setattr(api, "get_batch_video_generator", lambda: fake)
    monkeypatch.setattr(api, "prepare_video_model", lambda mid: (False, "Wan 2.2 5B is not installed."))
    resp = client.post("/api/batch-video/retry/VideoBatch_x")
    assert resp.status_code == 400 and not started


# ── legacy lora_name ─────────────────────────────────────────────────────────

def test_lora_name_resolves_only_to_a_catalog_lora_for_this_model(monkeypatch):
    monkeypatch.setitem(vmr.VIDEO_MODEL_REGISTRY, "user-lora-test", {
        "type": "lora", "applies_to": ["wan22-5b"],
        "files": [{"src": "x.safetensors", "dst": "loras/style_x.safetensors"}],
    })
    pick = ComfyUIVideoGenerator._lora_name_adapter_id
    assert pick("user-lora-test", "wan22-5b") == "user-lora-test"
    assert pick("style_x.safetensors", "wan22-5b") == "user-lora-test"
    # A LoRA for another model, or an image-model cast LoRA, is left out.
    assert pick("style_x.safetensors", "ltx23-distilled-fp8") is None
    assert pick("elara_sdxl.safetensors", "wan22-5b") is None
    assert pick(None, "wan22-5b") is None


# ── graph builders ───────────────────────────────────────────────────────────

def test_no_graph_helper_offers_freeu():
    assert not hasattr(ComfyUIVideoGenerator, "_add_freeu_node")
    assert not hasattr(ComfyUIVideoGenerator, "_add_lora_loader")


def test_minimax_reference_ranges_refuse_an_overflow_instead_of_overwriting():
    gen = ComfyUIVideoGenerator.__new__(ComfyUIVideoGenerator)
    with pytest.raises(ValueError, match="at most 3 videos"):
        gen._create_minimax_ref_workflow("p", ref_videos=[{"filename": "v.mp4"}] * 4)
    limits = vmr._H3_REF_LIMITS
    from backend.services.comfyui_video_workflows import _MINIMAX_REF_RANGES
    assert limits["images"] <= _MINIMAX_REF_RANGES["images"][1]
    assert limits["videos"] <= _MINIMAX_REF_RANGES["videos"][1]
    assert limits["audios"] <= _MINIMAX_REF_RANGES["audios"][1]


def test_post_nodes_take_ids_on_an_empty_graph():
    from backend.services.comfyui_video_workflows import _next_node_id
    assert _next_node_id({}) == 1
    assert _next_node_id({"4v": {}, "13b": {}, "9": {}}) == 10
