"""What an MCP client is told when a GPU job's service is stopped, and the
kind every failed GPU job reports.

The tools are called the way the MCP server calls them (transport "mcp").
Their calls to the Guaardvark backend go over HTTP to the backend's own routes
(tools, batch images, Audio Foundry proxy), answered by a Flask test client in
place of the network. ComfyUI and Audio Foundry are faked where the backend
reaches them over HTTP; everything between the tool and that call is real.
Automatic service start keeps its defaults (off for images, audio and Film
Crew renders; on for video renders) unless a test changes it.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from flask import Flask

from backend import config
from backend.api import audio_foundry_api as afa
from backend.api import batch_image_generation_api as bia
from backend.mcp import config as mcp_config
from backend.services import comfyui_image_generator as cig
from backend.services import plugin_bridge, tool_jobs
from backend.services import video_model_registry as vmr
from backend.services.batch_image_generator import (
    BatchGenerationStatus, BatchImageGenerator, BatchPrompt,
)
from backend.services.plugin_bridge import service_down_message
from backend.tools import audio_tools as at
from backend.tools import image_tools as it
from backend.utils import media_inputs as mi

BACKEND = "http://backend.test"
IMG = "/api/outputs/generated_images/photo.png"
AUDIO_JOB = "a" * 32
COMFY_SENTENCE = (
    "ComfyUI is not running. Start the ComfyUI plugin on the Plugins page "
    "(or POST /api/plugins/comfyui/start), or set {switch}=1 so Guaardvark starts it "
    "when a job needs it."
)


class _Reply:
    """What requests.request returns, built from the test client's response."""

    def __init__(self, resp):
        self.status_code = resp.status_code
        self.content = resp.get_data()
        self.text = resp.get_data(as_text=True)
        self._body = resp.get_json(silent=True)

    def json(self):
        if self._body is None:
            raise ValueError("not JSON")
        return self._body


def _refused(*args, **kwargs):
    raise requests.ConnectionError("connection refused")


@pytest.fixture
def starts(monkeypatch):
    """Automatic start at its defaults; records any start that is asked for."""
    for name in ("GUAARDVARK_JOB_SERVICE_START", "GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR",
                 "GUAARDVARK_PIPELINE_STAGE_PREP", "GUAARDVARK_MCP_PROCESS", "GUAARDVARK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    asked = []
    monkeypatch.setattr(plugin_bridge, "_try_start_plugin",
                        lambda plugin_id, **kw: asked.append(plugin_id) or (False, "no starts in tests"))
    return asked


@pytest.fixture
def backend(monkeypatch, starts, tmp_path):
    """The backend's routes at BACKEND; backend_http's calls are sent there."""
    from backend.api.tools_api import tools_bp

    app = Flask(__name__)
    for bp in (tools_bp, afa.audio_foundry_bp, bia.batch_image_bp):
        app.register_blueprint(bp)
    tools = [it.EditImageTool(), it.InpaintImageTool(), it.OutpaintImageTool(), it.VideoGeneratorTool()]
    by_name = {t.name: t for t in tools}
    app.tool_registry = SimpleNamespace(
        get_tool=by_name.get, execute_tool=lambda name, **params: by_name[name].execute(**params))
    client = app.test_client()

    def request(method, url, json=None, params=None, headers=None, timeout=None, **kw):
        assert url.startswith(BACKEND), f"unexpected network call to {url}"
        return _Reply(client.open(url[len(BACKEND):], method=method, json=json,
                                  query_string=params, headers=dict(headers or {})))

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setenv("GUAARDVARK_URL", BACKEND)
    monkeypatch.setattr(tool_jobs, "_jobs", OrderedDict())
    monkeypatch.setattr(mcp_config, "load_config", lambda: mcp_config.MCPConfig())

    # Files an MCP client may name: the install's outputs.
    root = tmp_path / "install"
    outputs = root / "data" / "outputs"
    (outputs / "generated_images").mkdir(parents=True)
    (outputs / "generated_images" / "photo.png").write_bytes(b"png")
    (root / "data" / "uploads").mkdir(parents=True)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(root / "data" / "uploads"))
    monkeypatch.setattr(config, "OUTPUT_DIR", str(outputs))
    monkeypatch.setattr(config, "GUAARDVARK_ROOT", root)
    monkeypatch.setattr(mi, "resources_root", lambda: str(outputs.resolve()))
    monkeypatch.setattr(it, "_chat_gpu_wait", lambda: None)
    return app


@pytest.fixture
def comfyui(monkeypatch):
    """ComfyUI over HTTP: down unless ``state["up"]``; when up, the prompt's
    history is ``state["history"]``. The editing packs are installed."""
    state = {"up": False, "history": None}

    def get(url, timeout=None, **kw):
        if not state["up"]:
            _refused()
        if "/history/" in url:
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"p1": state["history"]})
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {})

    def post(url, json=None, files=None, timeout=None, **kw):
        if not state["up"]:
            _refused()
        body = {"name": "photo.png"} if url.endswith("/upload/image") else {"prompt_id": "p1"}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr(cig, "requests", SimpleNamespace(get=get, post=post, exceptions=requests.exceptions))
    gen = cig.ComfyUIImageGenerator
    monkeypatch.setattr(gen, "qwen_edit_installed", lambda self: True)
    monkeypatch.setattr(gen, "pulid_installed", lambda self: True)
    return state


@pytest.fixture
def audio_foundry(monkeypatch):
    """Audio Foundry over HTTP: down unless ``state["up"]``; when up, a job
    read answers ``state["job"]`` and a submit queues it."""
    state = {"up": False, "job": None}

    def get(url, timeout=None, **kw):
        if not state["up"]:
            _refused()
        return SimpleNamespace(status_code=200, json=lambda: state["job"])

    def post(url, json=None, timeout=None, **kw):
        if not state["up"]:
            _refused()
        return SimpleNamespace(status_code=202, json=lambda: {"job_id": AUDIO_JOB, "status": "queued"})

    monkeypatch.setattr(afa, "requests", SimpleNamespace(
        get=get, post=post, delete=_refused, ConnectionError=requests.ConnectionError,
        Timeout=requests.Timeout, RequestException=requests.RequestException))
    monkeypatch.setattr(at, "SPEECH_POLL_S", 0.01)
    return state


def _mcp(cls):
    tool = cls()
    tool.set_context({"transport": "mcp"})
    return tool


# ---- a stopped service, automatic start off -------------------------------------------------
PHOTO_TOOLS = [
    (it.EditImageTool, {"instruction": "make it night", "image": IMG}),
    (it.InpaintImageTool, {"instruction": "remove the cup", "image": IMG}),
    (it.OutpaintImageTool, {"image": IMG}),
]


@pytest.mark.parametrize("cls,args", PHOTO_TOOLS, ids=[c.name for c, _ in PHOTO_TOOLS])
def test_photo_tools_name_a_stopped_comfyui_and_how_to_start_it(backend, comfyui, starts, cls, args):
    res = _mcp(cls).execute(**args, wait_for_result=True)
    assert res.success is False
    assert res.error.endswith(service_down_message("comfyui", context="image"))
    assert COMFY_SENTENCE.format(switch="GUAARDVARK_JOB_SERVICE_START") in res.error
    assert res.metadata["error_kind"] == "comfyui_down"
    assert starts == []

    # The job id reads the same failure, with its kind and next step.
    status = _mcp(it.GenerationStatusTool).execute(batch_id=res.metadata["job_id"])
    assert status.success and "failed" in status.output
    assert "Error: ComfyUI not running: " in status.output
    assert service_down_message("comfyui", context="image") in status.output
    assert status.metadata["failure"]["kind"] == "comfyui_down"


def test_identity_in_chat_names_a_stopped_comfyui(backend, comfyui, starts, monkeypatch, tmp_path):
    from backend.services import consent_records

    monkeypatch.setattr(consent_records, "has_consent", lambda path: True)
    face = tmp_path / "face.png"
    face.write_bytes(b"png")
    res = it.GenerateIdentityTool().execute(prompt="on a beach", image=str(face))
    assert res.success is False and res.metadata["error_kind"] == "comfyui_down"
    assert res.error.endswith(service_down_message("comfyui", context="image"))
    assert starts == []


def test_an_image_batch_item_names_a_stopped_comfyui_after_a_restart(backend, comfyui, monkeypatch, tmp_path):
    """generate_image queues a batch; an item on a ComfyUI model fails with the
    sentence and its kind, which the batch keeps on disk and status reads."""
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.base_output_dir = tmp_path / "Images"
    gen.active_batches, gen.batch_lock = {}, __import__("threading").Lock()
    folder = gen.base_output_dir / "ImageBatch_1"
    (folder / "images").mkdir(parents=True)
    status = BatchGenerationStatus(batch_id="ImageBatch_1", status="running", total_images=1,
                                   completed_images=0, failed_images=0, start_time=None,
                                   end_time=None, results=[], output_dir=str(folder))
    item = gen._generate_single_image("ImageBatch_1", BatchPrompt(id="p1", prompt="a fox", model="flux-dev"),
                                      folder, status)
    assert item.success is False and item.error_kind == "comfyui_down"
    status.results, status.status, status.failed_images = [item], "completed", 1
    gen._save_batch_metadata(status, folder)

    # A new process: the batch is read back from its folder.
    monkeypatch.setattr(bia, "get_batch_image_generator", lambda: gen)
    monkeypatch.setattr(bia, "service_available", True)
    res = _mcp(it.GenerationStatusTool).execute(batch_id="ImageBatch_1")
    assert res.success
    assert "Failed item: ComfyUI not running: " in res.output
    assert service_down_message("comfyui", context="image") in res.output
    assert "Next: Start the ComfyUI plugin" in res.output
    assert [f["kind"] for f in res.metadata["failures"]] == ["comfyui_down"]


@pytest.mark.parametrize("cls,args", [
    (at.GenerateMusicTool, {"style": "jazz"}),
    (at.GenerateSpeechTool, {"text": "Dinner is served.", "voice": "af_heart", "engine": "kokoro"}),
], ids=["generate_music", "generate_speech"])
def test_audio_tools_name_a_stopped_audio_foundry_and_how_to_start_it(
        backend, audio_foundry, starts, monkeypatch, cls, args):
    monkeypatch.setattr(at, "_kokoro_voice_ids", lambda: ["af_heart"])
    res = _mcp(cls).execute(**args)
    assert res.success is False
    assert res.error == service_down_message("audio_foundry", context="audio")
    assert res.error == (
        "Audio Foundry is not running. Start the Audio Foundry plugin on the Plugins page "
        "(or POST /api/plugins/audio_foundry/start), or set GUAARDVARK_JOB_SERVICE_START=1 so "
        "Guaardvark starts it when a job needs it.")
    assert res.metadata["error_kind"] == "service_down"
    assert starts == []


def test_an_audio_job_read_while_audio_foundry_is_stopped(backend, audio_foundry):
    res = _mcp(it.GenerationStatusTool).execute(batch_id=AUDIO_JOB)
    assert res.success and "unknown" in res.output
    assert service_down_message("audio_foundry", context="audio") in res.output
    assert res.metadata["failure"]["kind"] == "service_down"


def test_generate_video_with_automatic_start_off_names_a_stopped_comfyui(backend, starts, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    monkeypatch.setattr(vmr, "is_model_installed", lambda model_id: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)  # its HTTP probe
    res = _mcp(it.VideoGeneratorTool).execute(prompt="a fox in the snow", model="wan22-5b")
    assert res.success is False
    sentence = service_down_message("comfyui", context="video")
    assert sentence == COMFY_SENTENCE.format(switch="GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR")
    assert sentence in res.error and "requires ComfyUI." in res.error
    assert "then retry" not in res.error  # the model's own older advice is replaced, not repeated
    assert res.metadata["failure"]["kind"] == "comfyui_down"
    assert starts == []


def test_film_crew_names_a_stopped_comfyui_before_its_render(starts, monkeypatch):
    from backend.services.job_types import RenderErrorKind, RenderFailure
    from backend.tools import video_pipeline_tools as vpt

    monkeypatch.setattr(vmr, "preflight_video_model", lambda model_id: (
        False, RenderFailure(RenderErrorKind.COMFYUI_DOWN, "Wan 2.2 requires ComfyUI.")))
    note = vpt._comfyui_note("wan22-5b")
    assert note.endswith(COMFY_SENTENCE.format(switch="GUAARDVARK_JOB_SERVICE_START"))


def test_a_music3_song_with_comfyui_stopped_is_refused_with_its_kind(backend, starts, monkeypatch):
    monkeypatch.setattr(vmr, "is_model_installed", lambda model_id: True)
    monkeypatch.setattr(vmr, "_comfyui_reachable", lambda: False)
    model = next(m for m in vmr.VIDEO_MODEL_REGISTRY if m.startswith("minimax-music3"))
    r = backend.test_client().post("/api/audio-foundry/generate/music", json={"model": model, "style_prompt": "x"})
    body = r.get_json()
    assert r.status_code == 400 and body["error_kind"] == "comfyui_down"
    assert body["error"].endswith(COMFY_SENTENCE.format(switch="GUAARDVARK_JOB_SERVICE_START"))
    assert starts == []


@pytest.mark.parametrize("context,switch", [
    ("image", "GUAARDVARK_JOB_SERVICE_START"),
    ("audio", "GUAARDVARK_JOB_SERVICE_START"),
    ("film-crew", "GUAARDVARK_JOB_SERVICE_START"),
    ("video", "GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR"),
    ("music-video", "GUAARDVARK_PIPELINE_STAGE_PREP"),
])
def test_each_kind_of_job_names_the_setting_that_starts_its_service(starts, monkeypatch, context, switch):
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "1")
    if context == "video":
        monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    assert service_down_message("comfyui", context=context) == COMFY_SENTENCE.format(switch=switch)


def test_with_automatic_start_on_the_sentence_says_why_it_did_not_help(starts, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_JOB_SERVICE_START", "1")
    text = service_down_message("comfyui", context="image", why="plugin 'comfyui' is user-disabled")
    assert text == ("ComfyUI is not running. Start the ComfyUI plugin on the Plugins page "
                    "(or POST /api/plugins/comfyui/start). Automatic start did not bring it up: "
                    "plugin 'comfyui' is user-disabled.")
    # Both switches are named when both are off.
    monkeypatch.delenv("GUAARDVARK_JOB_SERVICE_START")
    monkeypatch.setenv("GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR", "0")
    assert ("set GUAARDVARK_JOB_SERVICE_START=1 and GUAARDVARK_PLUGIN_AUTO_ORCHESTRATOR=1 so"
            in service_down_message("audio_foundry", context="audio"))


# ---- failed jobs report their kind ------------------------------------------------------------
OOM_HISTORY = {"outputs": {}, "status": {"status_str": "error", "completed": False, "messages": [
    ["execution_error", {"node_id": "3", "node_type": "KSampler", "exception_type": "torch.OutOfMemoryError",
                         "exception_message": "CUDA out of memory. Tried to allocate 2.00 GiB"}]]}}


def _render_tail(self, **kw):
    """The end of every ComfyUI image render: wait for the prompt's history,
    then fetch its first image (the graph building and GPU admission before it
    are left out)."""
    outputs = self._wait("p1", timeout=5)
    if self._fetch_first_image(outputs or {}, kw["output_path"]) is None:
        raise RuntimeError("ComfyUI produced no edited image for prompt p1")


def test_a_photo_job_that_runs_out_of_memory_reports_oom(backend, comfyui, monkeypatch):
    comfyui.update(up=True, history=OOM_HISTORY)
    monkeypatch.setattr(cig.ComfyUIImageGenerator, "edit_image_qwen", _render_tail)
    res = _mcp(it.EditImageTool).execute(instruction="night", image=IMG, wait_for_result=True)
    assert res.success is False and res.metadata["error_kind"] == "oom"
    assert "ComfyUI ran out of GPU memory in KSampler (node 3)" in res.error
    status = _mcp(it.GenerationStatusTool).execute(batch_id=res.metadata["job_id"])
    assert "Error: Out of GPU memory: " in status.output
    assert "Next: Lower the size or steps, or turn off LoRAs." in status.output


@pytest.mark.parametrize("job,kind,says", [
    ({"error": "WeightsNotInstalled: ACE-Step is not installed", "error_kind": "model_not_installed"},
     "model_not_installed", "Next: Open Audio Studio → Manage models and install it."),
    ({"error": "OutOfMemoryError: CUDA out of memory", "error_kind": "oom"},
     "oom", "Next: Ask for a shorter song, or stop other GPU work first."),
    ({"error": "ValueError: bad lyrics"}, "unknown", "Next: logs/audio_foundry.log has the details."),
], ids=["model_not_installed", "oom", "unknown"])
def test_a_failed_song_reports_its_kind(backend, audio_foundry, job, kind, says):
    audio_foundry.update(up=True, job={"id": AUDIO_JOB, "intent": "music", "status": "error", **job})
    res = _mcp(it.GenerationStatusTool).execute(batch_id=AUDIO_JOB)
    assert res.success and res.metadata["failure"]["kind"] == kind
    assert says in res.output


def test_a_speech_job_that_fails_while_the_call_waits_reports_its_kind(backend, audio_foundry, monkeypatch):
    monkeypatch.setattr(at, "_kokoro_voice_ids", lambda: ["af_heart"])
    audio_foundry.update(up=True, job={"id": AUDIO_JOB, "intent": "voice", "status": "error",
                                       "error": "OutOfMemoryError: CUDA out of memory", "error_kind": "oom"})
    res = _mcp(at.GenerateSpeechTool).execute(text="Hello.", voice="af_heart", engine="kokoro")
    assert res.success is False and res.metadata == {"job_id": AUDIO_JOB, "error_kind": "oom"}
    assert "CUDA out of memory" in res.error


# ---- what generate_speech says about its wait ------------------------------------------------
def test_generate_speech_says_it_waits_and_for_how_long(backend, audio_foundry, monkeypatch):
    desc = at.GenerateSpeechTool.description
    assert ("Unlike the other generate tools, which over MCP answer at once with a job or batch id "
            "to poll, this waits for the file") in desc
    assert f"up to {tool_jobs.MAX_WAIT_S:.0f} s, or half the MCP per-call timeout" in desc
    # And it does: a job still running when the wait is over answers with its id.
    monkeypatch.setattr(at, "_kokoro_voice_ids", lambda: ["af_heart"])
    monkeypatch.setattr(at, "_speech_wait_s", lambda: 0.05)
    audio_foundry.update(up=True, job={"id": AUDIO_JOB, "intent": "voice", "status": "running"})
    res = _mcp(at.GenerateSpeechTool).execute(text="Hello.", voice="af_heart", engine="kokoro")
    assert res.success and res.output["job_id"] == AUDIO_JOB and "get_generation_status" in res.output["next"]
