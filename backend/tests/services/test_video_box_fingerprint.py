"""scripts/video_box_fingerprint.py: the environment allowlist (no other key is
ever printed), reading the graph VideoHelperSuite embeds in an MP4, the offline
graph build, and the --diff report."""
import importlib.util
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]

SECRETS = {
    "SECRET_KEY": "s3cr3t-flask-key",
    "DATABASE_URL": "postgresql://guaardvark:hunter2@localhost:5432/guaardvark",
    "HF_TOKEN": "hf_notarealtokenbutsecretlooking",
    "DISCORD_BOT_TOKEN": "discord-token-value",
}


def _fp():
    path = ROOT / "scripts" / "video_box_fingerprint.py"
    spec = importlib.util.spec_from_file_location("video_box_fingerprint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_no_secret(text: str):
    for key, value in SECRETS.items():
        assert key not in text, key
        assert value not in text, key
    assert "hunter2" not in text


# ── Environment allowlist ────────────────────────────────────────────────────

def _dotenv(path: Path) -> Path:
    lines = [f"{k}={v}" for k, v in SECRETS.items()] + [
        "# GUAARDVARK_WAN5B_SAMPLER=adaptive",
        "GUAARDVARK_COMFYUI_ATTENTION=ck",
        'export GUAARDVARK_WAN5B_SAMPLER="official"',
        "GUAARDVARK_GPU_POWER_LIMIT=max  # full board power",
        "NOT_ALLOWLISTED_SETTING=1",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_dotenv_yields_only_allowlisted_keys(tmp_path):
    fp = _fp()
    found = fp.parse_dotenv(_dotenv(tmp_path / ".env"))
    assert found == {"GUAARDVARK_COMFYUI_ATTENTION": "ck", "GUAARDVARK_WAN5B_SAMPLER": "official",
                     "GUAARDVARK_GPU_POWER_LIMIT": "max"}
    _assert_no_secret(json.dumps(found))


def test_process_environ_is_filtered_to_the_allowlist():
    fp = _fp()
    blob = b"\0".join(f"{k}={v}".encode() for k, v in {**SECRETS, "GUAARDVARK_COMFYUI_ATTENTION": "ck",
                                                       "CUDA_VISIBLE_DEVICES": "0", "PATH": "/usr/bin"}.items())
    assert fp.filter_environ(blob + b"\0") == {"CUDA_VISIBLE_DEVICES": "0", "GUAARDVARK_COMFYUI_ATTENTION": "ck"}


def _fake_proc(base: Path, root: Path, comfy_dir: Path) -> Path:
    """A /proc with a backend, a Celery worker, a ComfyUI and an unrelated process."""
    proc = base / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu 1 2 3\nbtime 1700000000\n")
    env = {**SECRETS, "GUAARDVARK_COMFYUI_ATTENTION": "ck", "GUAARDVARK_WAN_CLIP_DEVICE": "cpu"}
    procs = {
        "101": (["python", "-m", "backend.app"], root),
        "102": ([str(root / "backend/venv/bin/celery"), "-A", "backend.celery_app.celery", "worker",
                 "--hostname=main@%h"], root),
        "103": ([str(root / "backend/venv/bin/python"), "main.py", "--use-ck-attention"], comfy_dir),
        "104": (["python", "-m", "backend.app"], base),  # another install's backend
    }
    for pid, (argv, cwd) in procs.items():
        d = proc / pid
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0")
        (d / "stat").write_text(f"{pid} (python) S " + " ".join(["0"] * 18) + " 500 0 0\n")
        os.symlink(cwd, d / "cwd")
    return proc


def test_env_section_never_prints_a_secret(tmp_path):
    fp = _fp()
    root = tmp_path / "checkout"
    comfy_dir = root / "plugins" / "comfyui" / "ComfyUI"
    comfy_dir.mkdir(parents=True)
    _dotenv(root / ".env")
    proc = _fake_proc(tmp_path, root, comfy_dir)

    processes = fp.find_processes(root, comfy_dir, proc=proc)
    assert set(processes) == {"backend", "celery worker main", "comfyui"}
    assert processes["backend"]["env"] == {"GUAARDVARK_COMFYUI_ATTENTION": "ck", "GUAARDVARK_WAN_CLIP_DEVICE": "cpu"}

    shell = fp.allowlisted({**SECRETS, "GUAARDVARK_WAN5B_SAMPLER": "adaptive"})
    ctx = SimpleNamespace(root=root, shell_env=shell, processes=processes)
    section = fp.Scrubber(root)(fp.collect_env(ctx))
    text = json.dumps(section)
    _assert_no_secret(text)
    assert section["this_shell"] == {"GUAARDVARK_WAN5B_SAMPLER": "adaptive"}
    assert section["processes"]["comfyui"]["env"]["GUAARDVARK_COMFYUI_ATTENTION"] == "ck"


def test_the_printed_document_holds_no_secret(tmp_path, monkeypatch, capsys):
    fp = _fp()
    root = tmp_path / "checkout"
    root.mkdir()
    _dotenv(root / ".env")
    for key, value in SECRETS.items():
        monkeypatch.setenv(key, value)
    assert fp.main(["--root", str(root), "--only", "env,custom_nodes"]) == 0
    out = capsys.readouterr().out
    _assert_no_secret(out)
    assert json.loads(out)["env"]["dotenv"]["GUAARDVARK_COMFYUI_ATTENTION"] == "ck"


# ── The graph inside an MP4 ──────────────────────────────────────────────────

GRAPH = {
    "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors",
                                                 "weight_dtype": "default"}},
    "7": {"class_type": "Wan22ImageToVideoLatent", "inputs": {"width": 1280, "height": 704, "length": 121,
                                                              "batch_size": 1}},
    "8": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 8.0}},
    "10": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "seed": 5, "steps": 20, "cfg": 5.0,
                                                "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}},
}
# VideoHelperSuite's comment: {"prompt": "<the API graph as a JSON string>"}
COMMENT = json.dumps({"prompt": json.dumps(GRAPH)})


def _box(kind: bytes, payload: bytes, large: bool = False) -> bytes:
    if large:
        return struct.pack(">I4sQ", 1, kind, 16 + len(payload)) + payload
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4(*, quicktime_keys: bool = False) -> bytes:
    """ftyp, a 64-bit mdat, then moov (as a non-faststart file lays them out)."""
    hdlr = _box(b"hdlr", b"\0" * 8 + b"mdta" + b"\0" * 12 + b"\0")
    if quicktime_keys:
        key = _box(b"mdta", b"comment")
        keys = _box(b"keys", b"\0\0\0\0" + struct.pack(">I", 1) + key)
        item = _box(struct.pack(">I", 1), _box(b"data", struct.pack(">II", 1, 0) + COMMENT.encode()))
        meta = _box(b"meta", hdlr + keys + _box(b"ilst", item))  # QuickTime meta: no version/flags
        moov = _box(b"moov", _box(b"mvhd", b"\0" * 100) + meta)
    else:
        item = _box(b"\xa9cmt", _box(b"data", struct.pack(">II", 1, 0) + COMMENT.encode()))
        meta = _box(b"meta", b"\0\0\0\0" + hdlr + _box(b"ilst", item))
        moov = _box(b"moov", _box(b"mvhd", b"\0" * 100) + _box(b"udta", meta))
    ftyp = _box(b"ftyp", b"isom\0\0\x02\0isomiso2avc1mp41")
    return ftyp + _box(b"mdat", b"\0" * 256, large=True) + moov


@pytest.mark.parametrize("quicktime_keys", [False, True])
def test_graph_is_read_from_crafted_mp4_atoms(tmp_path, quicktime_keys):
    fp = _fp()
    clip = tmp_path / "wan22_5b_00001.mp4"
    clip.write_bytes(_mp4(quicktime_keys=quicktime_keys))
    assert fp.mp4_comment_atoms(clip) == COMMENT
    out = fp.read_mp4(clip, use_ffprobe=False)
    assert out["comment_source"] == "mp4 atoms" and out["graph"] == GRAPH
    s = out["summary"]
    assert (s["width"], s["height"], s["length"], s["shift"], s["sampler_name"], s["attention_pin"]) == (
        1280, 704, 121, 8.0, "uni_pc", None)


def test_a_clip_without_a_comment_says_so(tmp_path):
    fp = _fp()
    clip = tmp_path / "plain.mp4"
    clip.write_bytes(_box(b"ftyp", b"isom\0\0\x02\0isom") + _box(b"moov", _box(b"mvhd", b"\0" * 100)))
    out = fp.read_mp4(clip, use_ffprobe=False)
    assert out["graph"] is None and "no comment tag" in out["note"]


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_graph_is_read_from_an_ffmpeg_written_clip_both_ways(tmp_path):
    fp = _fp()
    clip = tmp_path / "wan22_5b_00002.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.2",
                    "-c:v", "mpeg4", "-metadata", f"comment={COMMENT}", str(clip)], check=True, timeout=60)
    by_atoms = fp.read_mp4(clip, use_ffprobe=False)
    by_probe = fp.read_mp4(clip, use_ffprobe=True)
    assert by_atoms["graph"] == by_probe["graph"] == GRAPH
    assert by_probe["comment_source"] == "ffprobe" and by_probe["stream"]["width"] == 64


# ── The graph the builder makes, offline ─────────────────────────────────────

@pytest.mark.parametrize("attention,pinned", [("ck", "pytorch attention"), ("pytorch", None)])
def test_offline_build_runs_the_real_builder(monkeypatch, attention, pinned):
    fp = _fp()
    monkeypatch.setenv("GUAARDVARK_COMFYUI_ATTENTION", attention)
    monkeypatch.delenv("GUAARDVARK_WAN_CLIP_DEVICE", raising=False)
    monkeypatch.delenv("GUAARDVARK_WAN5B_SAMPLER", raising=False)
    built = fp.build_wan5b_graph(lambda cls: True, 16000, fp.FIXED_REQUEST)
    s = fp.summarize_graph(built["workflow"])
    assert s["attention_pin"] == pinned
    assert (s["width"], s["height"], s["length"], s["steps"], s["cfg"]) == (1280, 704, 121, 20, 5.0)
    assert (s["sampler_name"], s["shift"], s["decode"], s["rife"]["multiplier"]) == ("uni_pc", 8.0,
                                                                                      "VAEDecodeTiled", 2)
    assert s["clip_device"] == "cpu"  # a 16 GB card is under the family's text-encoder threshold


def test_checks_name_graph_settings_that_differ_from_the_build():
    fp = _fp()
    built = fp.summarize_graph(GRAPH)
    clip = dict(GRAPH, **{"10": {"class_type": "KSampler", "inputs": {**GRAPH["10"]["inputs"],
                                                                     "sampler_name": "euler"}}})
    notes = fp.checks({"graph": {"summary": built},
                       "mp4": {"files": [{"path": "x/wan22_5b_00003.mp4", "summary": fp.summarize_graph(clip)}]}})
    assert notes == ["wan22_5b_00003.mp4 was rendered with a graph that differs from what this checkout builds "
                     "in: sampler_name 'euler' vs 'uni_pc'"]


# ── --diff ───────────────────────────────────────────────────────────────────

def test_diff_names_each_difference(tmp_path, capsys):
    fp = _fp()
    a = {"format": 1, "captured": "2026-10-07T00:00:00Z",
         "env": {"dotenv": {"GUAARDVARK_COMFYUI_ATTENTION": "ck"}, "processes": {"backend": {"pid": 1}}},
         "graph": {"summary": {"shift": 8.0, "sampler_name": "uni_pc"}, "workflow": GRAPH},
         "runtime": {"comfyui_python": {"torch": {"version": "2.6.0+cu124"}}}}
    b = json.loads(json.dumps(a))
    b["captured"] = "2026-10-08T00:00:00Z"
    b["env"]["processes"]["backend"]["pid"] = 2
    b["env"]["dotenv"] = {}
    b["graph"]["summary"]["shift"] = 3.0
    b["runtime"]["comfyui_python"]["torch"]["version"] = "2.8.0+cu128"
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a))
    pb.write_text(json.dumps(b))

    assert fp.main(["--diff", str(pa), str(pb)]) == 1
    out = capsys.readouterr().out
    assert "env.dotenv.GUAARDVARK_COMFYUI_ATTENTION\n      A: ck\n      B: (absent)" in out
    assert "graph.summary.shift\n      A: 8.0\n      B: 3.0" in out
    assert "runtime.comfyui_python.torch.version\n      A: 2.6.0+cu124\n      B: 2.8.0+cu128" in out
    assert "pid" not in out.split("\n\n", 1)[1] and "3 difference(s)." in out
    assert out.index("== graph ==") < out.index("== env ==") < out.index("== runtime ==")

    assert fp.main(["--diff", str(pa), str(pa)]) == 0
    assert "No differences." in capsys.readouterr().out
