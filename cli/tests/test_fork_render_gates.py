"""Reading a production, and starting a render (CLI_PLAN D6).

Two halves, and the split is the point:

* the read-only commands that show what the five agents produced — no D2 exception, no
  write, covered here because they need a populated payload that the read-only contract's
  single empty default cannot give them;
* the three render gates, which are the only named commands D2 permits to POST a decision
  route. Each must refuse without `--yes`, send nothing when it refuses, and name the stage
  it moves the production to.

The last test asserts the exception stayed one file wide, because that is what stops it
from becoming a rule.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from llx.main import app


def _run(cli_runner, args, *, expect: int = 0):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == expect, result.output
    return result


_PROD = {
    "id": 3,
    "name": "The Last Spark",
    "current_stage": "awaiting_approval",
    "shots": [
        {"id": 7, "scene_number": 1, "shot_number": 2, "description": "a kettle sings",
         "approved": False, "storyboard_image_path": "/tmp/sb7.png",
         "storyboard_image_url": "/api/production/3/storyboard/shot/7/image",
         "video_clip_path": None, "regen_count": 1},
        {"id": 8, "scene_number": 1, "shot_number": 3, "description": "steam",
         "approved": False, "storyboard_image_path": None,
         "video_clip_path": "/tmp/clip8.mp4", "regen_count": 0},
    ],
}


# --- the read-only half ----------------------------------------------------

def test_subjects_shows_who_still_needs_a_lora(fake_backend, cli_runner, isolated_home):
    """`cast_required` + no LoRA is exactly what blocks confirm-casting."""
    fake_backend.route("GET", "/api/production/3/subjects", json={"subjects": [
        {"id": 1, "name": "sage", "kind": "character", "training_status": "trained",
         "lora_path": "/l/sage.safetensors", "cast_required": True},
        {"id": 2, "name": "Microphone", "kind": "prop", "training_status": None,
         "lora_path": None, "cast_required": False},
    ]})

    payload = json.loads(_run(cli_runner, ["film-crew", "subjects", "3", "--json"]).output)

    assert [s["id"] for s in payload["data"]] == [1, 2]
    assert payload["data"][0]["cast_required"] is True
    assert payload["data"][1]["cast_required"] is False


def test_shots_reports_what_exists_for_each_shot(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/3", json=_PROD)

    payload = json.loads(_run(cli_runner, ["film-crew", "shots", "3", "--json"]).output)

    assert [s["id"] for s in payload["data"]] == [7, 8]
    # The resumable-render state: storyboard but no clip is "not rendered yet".
    assert payload["data"][0]["storyboard_image_path"] and not payload["data"][0]["video_clip_path"]


def test_shot_prints_one_shot_and_downloads_its_frame(fake_backend, cli_runner, isolated_home, tmp_path):
    fake_backend.route("GET", "/api/production/3", json=_PROD)
    fake_backend.route("GET", "/api/production/3/storyboard/shot/7/image",
                       status=200, json=None, text="PNG")
    dest = tmp_path / "sb7.png"

    result = _run(cli_runner, ["film-crew", "shot", "3", "7", "--image", str(dest), "--json"])

    assert json.loads(result.output)["data"]["shot"]["id"] == 7
    assert dest.read_bytes() == b"PNG"
    assert len(fake_backend.calls_for("GET", "/api/production/3/storyboard/shot/7/image")) == 1


def test_shot_refuses_a_frame_that_does_not_exist_yet(fake_backend, cli_runner, isolated_home, tmp_path):
    """Shot 8 has no storyboard; asking for it must say so, not write an empty file."""
    fake_backend.route("GET", "/api/production/3", json=_PROD)
    dest = tmp_path / "sb8.png"

    result = _run(cli_runner, ["film-crew", "shot", "3", "8", "--image", str(dest)], expect=1)

    assert "no storyboard image" in result.output
    assert not dest.exists()


def test_templates_lists_the_screenwriters_starting_points(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/script-templates", json={"templates": [
        {"filename": "three_act.tmplt.txt", "name": "Three Act", "size_bytes": 900},
    ]})

    payload = json.loads(_run(cli_runner, ["film-crew", "templates", "--json"]).output)

    assert payload["data"][0]["filename"] == "three_act.tmplt.txt"


# The payload shapes below are the REAL ones, taken from the code that writes them rather
# than from this module's reading of them:
#   cut_plan[] — index, start_s, end_s, energy, section_label      (timing only, NO prompt)
#   clips[]    — index, start, end, clip_path, status, prompt,    (music_video_tasks.py:599)
#                duration_seconds, transition_to_next, filter_preset
# The first version of these tests invented `cut_plan[].prompt` and `clips[].path`, so they
# passed against a schema that does not exist: the `cuts` prompt column and the `clips` file
# column are both blank against every real project. Running the commands live found it.
# Keep these faithful — a fixture invented from the implementation cannot catch this class.
_CUT_PLAN = [
    {"index": 0, "start_s": 0.0, "end_s": 3.04, "energy": 0.8, "section_label": "intro"},
    {"index": 1, "start_s": 3.04, "end_s": 5.17, "energy": 0.4, "section_label": "verse"},
]
_CLIPS = [
    {"index": 0, "start": 0.0, "end": 3.04, "clip_path": "/out/cut_0.mp4",
     "status": "done", "prompt": "neon rain on wet asphalt", "duration_seconds": 3.04,
     "transition_to_next": "fade", "filter_preset": "noir"},
    {"index": 1, "start": 3.04, "end": 5.17, "clip_path": None,
     "status": "failed", "prompt": "a hand on the wheel", "duration_seconds": 2.13,
     "transition_to_next": None, "filter_preset": None, "error": "ComfyUI not reachable"},
]


def test_cuts_shows_the_prompts_that_live_on_the_clips(fake_backend, cli_runner, isolated_home):
    """The review the gate exists for: the prompt comes from `clips`, the section from `cut_plan`."""
    fake_backend.route("GET", "/api/music-video/1",
                       json={"id": 1, "cut_plan": _CUT_PLAN, "clips": _CLIPS})

    payload = json.loads(_run(cli_runner, ["music-video", "cuts", "1", "--json"]).output)

    rows = payload["data"]
    assert [r["index"] for r in rows] == [0, 1]
    assert all(r["prompt"] for r in rows), "every cut in a real plan carries a prompt"
    assert "neon rain" in rows[0]["prompt"]


def test_cuts_works_without_a_cut_plan(fake_backend, cli_runner, isolated_home):
    """`clips` alone is enough: it carries index, start and end as well."""
    fake_backend.route("GET", "/api/music-video/1", json={"id": 1, "cut_plan": [], "clips": _CLIPS})

    rows = json.loads(_run(cli_runner, ["music-video", "cuts", "1", "--json"]).output)["data"]

    assert [r["index"] for r in rows] == [0, 1]


def test_clips_reads_clip_path_not_path(fake_backend, cli_runner, isolated_home):
    """`clips[].clip_path` is the rendered file; `path` never exists."""
    fake_backend.route("GET", "/api/music-video/1", json={"id": 1, "cut_plan": [], "clips": _CLIPS})

    result = _run(cli_runner, ["music-video", "clips", "1"])

    assert "cut_0.mp4" in result.output, "the rendered file must show up"
    assert "failed" in result.output
    assert "ComfyUI not reachable" in result.output


def test_storyboard_downloads_a_cut_still(fake_backend, cli_runner, isolated_home, tmp_path):
    fake_backend.route("GET", "/api/music-video/1/storyboard/4", status=200, json=None, text="PNG")
    dest = tmp_path / "cut4.png"

    _run(cli_runner, ["music-video", "storyboard", "1", "4", "--out", str(dest), "--json"])

    assert dest.read_bytes() == b"PNG"


# --- the branch `--json` does not reach ------------------------------------
# These call the plain form on purpose. `--json` returns before the table/kv code, so a
# test suite that only ever passes `--json` never executes the human-readable half of a
# command — which is how a missing `from llx import output` in both new modules survived 17
# green tests and only surfaced against the live backend (2026-10-05). One per module is
# enough to keep that hole closed.

def test_subjects_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/3/subjects", json={"subjects": [
        {"id": 1, "name": "sage", "kind": "character", "training_status": "trained",
         "lora_path": "/l/sage.safetensors", "cast_required": True},
    ]})

    result = _run(cli_runner, ["film-crew", "subjects", "3"])

    assert "sage" in result.output


def test_shots_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/3", json=_PROD)

    result = _run(cli_runner, ["film-crew", "shots", "3"])

    assert "a kettle sings" in result.output or "7" in result.output


def test_shot_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/production/3", json=_PROD)

    result = _run(cli_runner, ["film-crew", "shot", "3", "7"])

    assert "a kettle sings" in result.output


def test_cuts_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/music-video/1",
                       json={"id": 1, "cut_plan": _CUT_PLAN, "clips": _CLIPS})

    result = _run(cli_runner, ["music-video", "cuts", "1"])

    assert "neon rain" in result.output


def test_clips_print_path_runs(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/music-video/1", json={"id": 1, "cut_plan": [], "clips": _CLIPS})

    result = _run(cli_runner, ["music-video", "clips", "1"])

    assert "done" in result.output


# --- the three gates -------------------------------------------------------

@pytest.mark.parametrize("args, path", [
    (["film-crew", "confirm-casting", "3"], "/api/production/3/casting/confirm"),
    (["film-crew", "approve-storyboard", "3"], "/api/production/3/storyboard/approve"),
    (["music-video", "approve", "1"], "/api/music-video/1/approve"),
])
def test_a_gate_refuses_without_yes_and_sends_nothing(fake_backend, cli_runner, isolated_home, args, path):
    fake_backend.default(json={"current_stage": "rendering"})

    result = _run(cli_runner, args, expect=1)

    assert fake_backend.calls == [], "a refused gate must not reach the backend"
    assert "without --yes" in result.output
    assert "CONFIRMATION_REQUIRED" in result.output


@pytest.mark.parametrize("args, path", [
    (["film-crew", "confirm-casting", "3"], "/api/production/3/casting/confirm"),
    (["film-crew", "approve-storyboard", "3"], "/api/production/3/storyboard/approve"),
    (["music-video", "approve", "1"], "/api/music-video/1/approve"),
])
def test_a_gate_posts_its_route_with_yes(fake_backend, cli_runner, isolated_home, args, path):
    fake_backend.default(json={"current_stage": "rendering", "shots_approved": 2})

    payload = json.loads(_run(cli_runner, [*args, "--yes", "--json"]).output)

    assert fake_backend.posted_paths() == [path]
    assert payload["status"] == "success"


def test_the_storyboard_gate_names_the_stage_it_starts(fake_backend, cli_runner, isolated_home):
    """Rendering is the irreversible-ish step, so the refusal must say what it does."""
    fake_backend.default(json={})

    result = _run(cli_runner, ["film-crew", "approve-storyboard", "3"], expect=1)

    assert "start the render" in result.output
    assert "rendering" in result.output


def test_the_named_gate_and_the_escape_hatch_agree(fake_backend, cli_runner, isolated_home):
    """Both must gate the same route the same way — one rule, two front doors."""
    fake_backend.default(json={})

    named = _run(cli_runner, ["film-crew", "approve-storyboard", "3"], expect=1)
    hatch = _run(cli_runner, ["api", "request", "POST", "/api/production/3/storyboard/approve"], expect=2)

    assert "without --yes" in named.output
    assert "needs --yes" in hatch.output
    assert fake_backend.calls == []


# --- the exception must stay one file wide ---------------------------------

_GATE_ROUTES = (
    "/api/production/%d/casting/confirm",
    "/api/production/%d/storyboard/approve",
    "/api/music-video/%d/approve",
)


def test_only_render_gates_names_a_gate_route():
    """The D6 exception is one file, so it cannot become a rule by accident.

    Same scan the D2 contract test runs — string literals outside docstrings — narrowed to
    the three templates. A fourth module naming one of these fails here, which is the
    conversation D6 is meant to force.
    """
    fork_dir = Path(__file__).resolve().parents[1] / "llx" / "commands" / "_fork"
    offenders = []
    for path in sorted(fork_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if any(route in node.value for route in _GATE_ROUTES) and path.name != "render_gates.py":
                offenders.append(f"{path.name}:{node.lineno}: {node.value!r}")

    assert not offenders, (
        "only render_gates.py may name a render gate (CLI_PLAN D6):\n  " + "\n  ".join(offenders)
    )
