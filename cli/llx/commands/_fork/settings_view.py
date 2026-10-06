"""Show the settings that produced an artifact (issue #8, part 2).

`images`/`videos` already record `retry_data.params`; `music-video` records `settings_json`;
`film-crew` records `settings_json`. `--json` already passes those through, but the human
`status` views dropped them, so a person reading the terminal could not see what produced
the artifact or feed it back into a re-run. These overrides delegate the `--json` path to
upstream unchanged and add the recorded-settings block to the human path only.

Delegation keeps the signatures upstream-shaped, including every option: leaving one out
would pass Typer's `OptionInfo` object through as a truthy default (the `--wait` bug), so
each override names the upstream command's full parameter list.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client
from llx.commands import film_crew as _film_crew
from llx.commands import images as _images
from llx.commands import music_video as _music_video
from llx.commands import videos as _videos
from llx.commands.film_crew import film_crew_app
from llx.commands.images import images_app
from llx.commands.music_video import music_video_app
from llx.commands.videos import videos_app

from ._common import fail, resolve_server

# The upstream groups these overrides extend; see `_fork/registry.py`.
EXTENDS = {
    "film-crew": film_crew_app,
    "images": images_app,
    "music-video": music_video_app,
    "videos": videos_app,
}


def _fmt(value) -> str:
    text = str(value)
    return text if len(text) <= 70 else text[:67] + "…"


def _is_json(json_out: bool) -> bool:
    from llx.global_opts import get_global_json

    return bool(json_out or get_global_json() or output.is_pipe())


def _fetch(endpoint: str, server):
    try:
        data = get_client(resolve_server(server)).get(endpoint)
    except LlxError as exc:
        fail(exc)
    return data.get("data", data) if isinstance(data, dict) else data


def _table(title: str, mapping: dict) -> None:
    if not mapping:
        return
    output.print_table(
        [{"setting": k, "value": _fmt(v)} for k, v in mapping.items()],
        columns=["setting", "value"],
        title=title,
    )


def _retry_settings(kind: str, endpoint: str, server) -> None:
    """Print `retry_data` (prompts + params) for an image/video batch."""
    row = _fetch(endpoint, server)
    retry = (row or {}).get("retry_data") if isinstance(row, dict) else None
    if not retry:
        output.print_warning(
            f"no recorded settings for this {kind} -- nothing to reproduce (old or "
            "editor-made batch)"
        )
        return
    prompts = retry.get("prompts") or ([retry["prompt"]] if retry.get("prompt") else [])
    if prompts:
        output.print_panel("Recorded prompt(s)", "\n".join(str(p) for p in prompts))
    _table(f"Recorded settings ({kind})", retry.get("params") or {})


@images_app.command("status")
def images_status(
    batch_id: str = typer.Argument(..., help="Batch ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check generation status of a batch."""
    if _is_json(json_out):
        return _images.images_status(batch_id=batch_id, server=server, json_out=json_out)
    _images.images_status(batch_id=batch_id, server=server, json_out=False)
    _retry_settings("image", f"/api/batch-image/status/{batch_id}", server)


@videos_app.command("status")
def videos_status(
    batch_id: str = typer.Argument(..., help="Batch ID"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Check generation status of a video batch."""
    if _is_json(json_out):
        return _videos.videos_status(batch_id=batch_id, server=server, json_out=json_out)
    _videos.videos_status(batch_id=batch_id, server=server, json_out=False)
    _retry_settings("video", f"/api/batch-video/status/{batch_id}", server)


@music_video_app.command("status")
def music_video_status(
    mv_id: int = typer.Argument(..., help="Music-video id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show one music-video project."""
    if _is_json(json_out):
        return _music_video.music_video_status(mv_id=mv_id, server=server, json_out=json_out)
    _music_video.music_video_status(mv_id=mv_id, server=server, json_out=False)
    row = _fetch(f"/api/music-video/{mv_id}", server) or {}
    cast = row.get("subject_ids") or []
    output.print_kv(
        {
            "Cast (subject_ids)": ", ".join(str(s) for s in cast) or "none",
            "LoRA consistency": "on" if row.get("use_lora_consistency") else "off",
            "Keyframe model": row.get("keyframe_model", ""),
            "I2V model": row.get("i2v_model", ""),
        },
        title="Recorded inputs",
    )
    treatment = row.get("user_treatment")
    if treatment:
        output.print_panel("User treatment", treatment)
    _table("Recorded settings", row.get("settings") or {})


@film_crew_app.command("status")
def film_crew_status(
    prod_id: int = typer.Argument(..., help="Production id"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show one Film Crew production."""
    if _is_json(json_out):
        return _film_crew.film_crew_status(prod_id=prod_id, server=server, json_out=json_out)
    _film_crew.film_crew_status(prod_id=prod_id, server=server, json_out=False)
    row = _fetch(f"/api/production/{prod_id}", server) or {}
    settings = row.get("settings_json") or {}
    if settings:
        _table("Recorded settings", settings)
    else:
        output.print_warning(
            "no recorded settings for this production -- it was created before settings were "
            "persisted (or without one); the screenplay is in `--json` under script_text"
        )
