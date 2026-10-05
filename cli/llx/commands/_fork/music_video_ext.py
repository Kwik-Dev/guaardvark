"""Adds `guaardvark music-video cuts|clips|storyboard` — the Director's actual plan.

Same extension pattern as `film_crew_ext.py`: no `COMMAND_NAME`, no `app`, so the registry
mounts nothing and the import registers these on the upstream `music_video_app` object —
which is upstream-owned (`llx/commands/music_video.py`) and stays unedited.

What was missing: `music-video status` printed `Cuts: 4` and `Clips: 0/4`. The whole point
of the approval gate is that a person reviews the cut plan and the per-cut prompts *before*
any GPU is spent, and the terminal could not show either. `cuts` is the review, `clips` is
the progress, `storyboard` is the frame.

Read-only, so no D2 exception is needed. `music-video approve` — the gate that releases the
spend — lives in `render_gates.py` (CLI_PLAN D6).
"""
from __future__ import annotations

from pathlib import Path

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands.music_video import music_video_app

from ._common import fail, json_mode, pick_list, resolve_server, success

_BASE = "/api/music-video"


def _client(server):
    return get_client(resolve_server(server))


@music_video_app.command("cuts")
def mv_cuts(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    prompts: bool = typer.Option(False, "--prompts", help="Print each cut's full prompt"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """The Director's cut list: when each cut starts, and what happens in it.

    This is the review the approval gate exists for. Nothing renders until a person has
    seen these prompts, and `--prompts` prints them in full rather than truncated.
    """
    as_json = json_mode(json_out)
    try:
        data = _client(server).get(f"{_BASE}/{mv_id}")
    except LlxError as exc:
        fail(exc)
    plan = pick_list(data, "cut_plan")
    clips = pick_list(data, "clips")

    # The per-cut prompt lives on `clips[]`, NOT on `cut_plan`. `cut_plan` is timing only —
    # `index, start_s, end_s, energy, section_label` — while the Director's prompt is written
    # into the clip dict when analysis finishes (music_video_tasks.py:599). Reading the prompt
    # off the cut plan printed a blank column against every real project; running this against
    # the live backend is what caught it (2026-10-05). So the two are joined by index, and
    # `clips` alone is enough when there is no cut plan.
    plan_by_index = {int(cut.get("index", i)): cut for i, cut in enumerate(plan)}
    source = clips or plan

    if as_json:
        success(source)
        return
    if not source:
        output.print_warning(
            "no cut plan yet — the analyzer has not run, or it is still running. "
            f"`music-video status {mv_id}` shows the stage."
        )
        return

    def _moment(value):
        return f"{value:.2f}" if isinstance(value, (int, float)) else (value or "")

    rows = []
    for i, cut in enumerate(source):
        idx = int(cut.get("index", i))
        meta = plan_by_index.get(idx, {})
        rows.append(
            {
                "idx": idx,
                "start": _moment(cut.get("start", meta.get("start_s"))),
                "end": _moment(cut.get("end", meta.get("end_s"))),
                "section": meta.get("section_label", ""),
                "prompt": (cut.get("prompt") or "")[:70],
            }
        )
    output.print_table(rows, columns=["idx", "start", "end", "section", "prompt"],
                        title=f"Cuts ({len(rows)})")
    if prompts:
        for row, cut in zip(rows, source):
            text = (cut.get("prompt") or "").strip()
            if text:
                output.print_panel(f"Cut {row['idx']}", text)


@music_video_app.command("clips")
def mv_clips(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Per-cut clip state: which cuts are rendered, and where each clip landed.

    `music-video status` gives `0/4`; this says which of the four, and whether a cut is
    queued, failed or done — the difference between "wait" and "something is stuck".
    """
    as_json = json_mode(json_out)
    try:
        data = _client(server).get(f"{_BASE}/{mv_id}")
    except LlxError as exc:
        fail(exc)
    clips = pick_list(data, "clips")
    if as_json:
        success(clips)
        return
    if not clips:
        output.print_warning("no clips yet — approve the cut plan to release generation")
        return
    output.print_table(
        [
            {
                "idx": c.get("index", ""),
                "status": c.get("status", ""),
                # `clip_path` is the key the writer sets (music_video_tasks.py:956); `path`
                # never appears, and reading it left this column permanently empty.
                "clip": Path(str(c["clip_path"])).name if c.get("clip_path") else "",
                "storyboard": "yes" if c.get("storyboard_path") else "no",
                "error": (str(c.get("error") or ""))[:40],
            }
            for c in clips
        ],
        columns=["idx", "status", "clip", "storyboard", "error"],
        title=f"Clips ({len(clips)})",
    )


@music_video_app.command("storyboard")
def mv_storyboard(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    idx: int = typer.Argument(..., help="Cut index (from `music-video cuts`)"),
    out: str = typer.Option(..., "--out", "-o", help="Write the PNG here"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Download one cut's storyboard still.

    The route serves a PNG, so this is a download rather than a listing — the cut's
    `storyboard_path` shows up in `music-video clips`.
    """
    as_json = json_mode(json_out)
    dest = Path(out).expanduser()
    try:
        _client(server).download(f"{_BASE}/{mv_id}/storyboard/{idx}", dest)
    except LlxError as exc:
        fail(exc)
    if as_json:
        success({"music_video_id": mv_id, "index": idx, "image": str(dest)})
        return
    output.print_success(f"Cut {idx} storyboard written to {dest}")
