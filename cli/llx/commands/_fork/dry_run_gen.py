"""`--dry-run` for the upstream generation commands (issue #8, part 1).

Every generation command should be able to show the request it would send, with the
resolved inputs and settings, before anything is spent. The upstream command files are
upstream-owned (`docs/CLI_SPEC.md` §11), so each command is re-registered here on its own
upstream Typer app -- `llx/main.py` imports `_fork.registry` after the upstream modules,
so these registrations win. Real runs delegate straight back to the upstream function:
only the dry-run path goes through `dry_run.preview`, and it runs the *real* function with
its write seams intercepted, so the preview cannot drift from what is actually sent.

`--dry-run` sends no write and takes no GPU lock. It may issue read-only GETs, because a
command resolves the active model before it builds its body; see `dry_run.py`.
"""
from __future__ import annotations

from pathlib import Path

import typer

from llx.commands import audio as _audio
from llx.commands import generate as _generate
from llx.commands import images as _images
from llx.commands import videos as _videos
from llx.commands.audio import audio_app
from llx.commands.generate import generate_app
from llx.commands.images import images_app
from llx.commands.videos import videos_app

from .dry_run import preview

# The upstream groups these overrides extend, so the REPL catalog and `/help` offer the
# new option; see `_fork/registry.py`.
EXTENDS = {
    "audio": audio_app,
    "generate": generate_app,
    "images": images_app,
    "videos": videos_app,
}

_DRY = typer.Option(False, "--dry-run", help="Show the request and resolved settings; send nothing")


def _explicit(**pairs) -> set:
    """Body keys the user supplied, for provenance: a key is explicit only when its flag
    was actually given. Flags with a standing default (guidance, motion) stay 'command
    default' -- passing the default by hand is indistinguishable, and saying otherwise
    would be a guess. Arguments are body-key -> the CLI value, so a flag whose name differs
    from its body key (``--steps`` -> ``num_inference_steps``) is still labelled right."""
    return {key for key, value in pairs.items() if value is not None and value is not False}


@images_app.command("generate")
def images_generate(
    prompt: str = typer.Argument(None, help="Image description prompt"),
    from_file: Path = typer.Option(None, "--from-file", "-f", exists=True, dir_okay=False,
                                   help="Text file with one prompt per line; all go in one batch"),
    count: int = typer.Option(1, "--count", "-n", help="Number of images"),
    model: str = typer.Option(None, "--model", "-m", help="Model override"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate images from a prompt."""
    if not dry_run:
        return _images.images_generate(prompt=prompt, from_file=from_file, count=count,
                                       model=model, server=server, json_out=json_out)
    preview("images generate",
            lambda: _images.images_generate(prompt=prompt, from_file=from_file, count=count,
                                            model=model, server=server, json_out=True),
            inputs=("prompts",),
            explicit=_explicit(model=model) | {"prompts"},
            json_out=json_out,
            notes=("the model is resolved on send (the active image model when --model is "
                   "omitted) and server-side clamps are applied then; only client-known fields "
                   "are shown here",))


@generate_app.command("image")
def generate_image(
    prompt: str = typer.Argument(..., help="Image description"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate an image from a prompt."""
    if not dry_run:
        return _generate.generate_image(prompt=prompt, server=server, json_out=json_out)
    preview("generate image",
            lambda: _generate.generate_image(prompt=prompt, server=server, json_out=True),
            inputs=("prompts",),
            explicit={"prompts"},
            json_out=json_out,
            notes=("the model is resolved on send (the active image model) and server-side "
                   "clamps are applied then; only client-known fields are shown here",))


@generate_app.command("csv")
def generate_csv(
    prompt: str = typer.Argument(..., help="Generation prompt"),
    output_file: str = typer.Option("output.csv", "--output", "-o", help="Output filename"),
    client_name: str = typer.Option(None, "--client", "-c", help="Client name"),
    project_name: str = typer.Option(None, "--project", "-p", help="Project name"),
    word_count: int = typer.Option(500, "--words", "-w", help="Target word count"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate CSV content from a prompt."""
    if not dry_run:
        return _generate.generate_csv(prompt=prompt, output_file=output_file, client_name=client_name,
                                      project_name=project_name, word_count=word_count,
                                      server=server, json_out=json_out)
    preview("generate csv",
            lambda: _generate.generate_csv(prompt=prompt, output_file=output_file, client_name=client_name,
                                           project_name=project_name, word_count=word_count,
                                           server=server, json_out=True),
            inputs=("prompt",), explicit={"target_word_count"} | ({"client"} if client_name else set())
            | ({"project"} if project_name else set()),
            json_out=json_out)


@videos_app.command("generate")
def videos_generate(
    prompt: str = typer.Argument(..., help="Video description prompt"),
    count: int = typer.Option(1, "--count", "-n", help="Number of videos to generate"),
    model: str = typer.Option(None, "--model", "-m", help="Registry model id (default: active video model)"),
    duration: int = typer.Option(None, "--duration", "-d", help="Duration in frames (default: model native)"),
    fps: int = typer.Option(None, "--fps", help="Output frame rate (default: model native)"),
    width: int = typer.Option(None, "--width", "-W", help="Video width (default: model native)"),
    height: int = typer.Option(None, "--height", "-H", help="Video height (default: model native)"),
    steps: int = typer.Option(None, "--steps", help="Inference steps (default: model floor)"),
    guidance: float = typer.Option(7.5, "--guidance", help="Guidance scale"),
    motion: float = typer.Option(1.0, "--motion", help="Motion strength"),
    seed: int = typer.Option(None, "--seed", help="Random seed for reproducibility"),
    frames_only: bool = typer.Option(False, "--frames-only", help="Generate frames without combining"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for completion with progress"),
    save: Path = typer.Option(None, "--save", help="With --wait: download the finished clip to this file"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate videos from a text prompt."""
    if not dry_run:
        return _videos.videos_generate(
            prompt=prompt, count=count, model=model, duration=duration, fps=fps, width=width,
            height=height, steps=steps, guidance=guidance, motion=motion, seed=seed,
            frames_only=frames_only, wait=wait, save=save, server=server, json_out=json_out)
    preview("videos generate",
            lambda: _videos.videos_generate(
                prompt=prompt, count=count, model=model, duration=duration, fps=fps, width=width,
                height=height, steps=steps, guidance=guidance, motion=motion, seed=seed,
                frames_only=frames_only, wait=False, save=None, server=server, json_out=True),
            inputs=("prompts",),
            explicit=_explicit(model=model, duration_frames=duration, fps=fps, width=width,
                               height=height, num_inference_steps=steps, seed=seed,
                               generate_frames_only=frames_only) | {"prompts"},
            json_out=json_out,
            notes=("server clamps (model duration/fps/steps floors) are applied on send and are "
                   "not shown here",))


@videos_app.command("from-image")
def videos_from_image(
    image_path: str = typer.Argument(..., help="Path to source image"),
    count: int = typer.Option(1, "--count", "-n", help="Number of videos to generate"),
    model: str = typer.Option(None, "--model", "-m", help="Registry model id (default: active I2V model)"),
    duration: int = typer.Option(None, "--duration", "-d", help="Duration in frames (default: model native)"),
    fps: int = typer.Option(None, "--fps", help="Output frame rate (default: model native)"),
    width: int = typer.Option(None, "--width", "-W", help="Video width (default: model native)"),
    height: int = typer.Option(None, "--height", "-H", help="Video height (default: model native)"),
    steps: int = typer.Option(None, "--steps", help="Inference steps (default: model floor)"),
    guidance: float = typer.Option(7.5, "--guidance", help="Guidance scale"),
    motion: float = typer.Option(1.0, "--motion", help="Motion strength"),
    seed: int = typer.Option(None, "--seed", help="Random seed for reproducibility"),
    frames_only: bool = typer.Option(False, "--frames-only", help="Generate frames without combining"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for completion with progress"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate videos from a source image."""
    if not dry_run:
        return _videos.videos_from_image(
            image_path=image_path, count=count, model=model, duration=duration, fps=fps,
            width=width, height=height, steps=steps, guidance=guidance, motion=motion,
            seed=seed, frames_only=frames_only, wait=wait, server=server, json_out=json_out)
    preview("videos from-image",
            lambda: _videos.videos_from_image(
                image_path=image_path, count=count, model=model, duration=duration, fps=fps,
                width=width, height=height, steps=steps, guidance=guidance, motion=motion,
                seed=seed, frames_only=frames_only, wait=False, server=server, json_out=True),
            inputs=("image_paths",),
            explicit=_explicit(model=model, duration_frames=duration, fps=fps, width=width,
                               height=height, num_inference_steps=steps, seed=seed,
                               generate_frames_only=frames_only) | {"image_paths"},
            json_out=json_out,
            notes=("server clamps (model duration/fps/steps floors) are applied on send and are "
                   "not shown here",))


@videos_app.command("combine")
def videos_combine(
    batch_id: str = typer.Argument(..., help="Batch ID with generated frames"),
    fps: int = typer.Option(7, "--fps", help="Output frame rate"),
    item_id: str = typer.Option(None, "--item", help="Specific item ID (omit for all)"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Combine generated frames into a video."""
    if not dry_run:
        return _videos.videos_combine(batch_id=batch_id, fps=fps, item_id=item_id,
                                      server=server, json_out=json_out)
    preview("videos combine",
            lambda: _videos.videos_combine(batch_id=batch_id, fps=fps, item_id=item_id,
                                           server=server, json_out=True),
            inputs=("item_id",), explicit=_explicit(item_id=item_id), json_out=json_out)


@audio_app.command("music")
def audio_music(
    style: str = typer.Argument(..., help="The sound you want: genre, instruments, mood, tempo"),
    lyrics: str = typer.Option(None, "--lyrics", "-l", help="Words to sing, or a path to a text file of them"),
    seconds: float = typer.Option(30.0, "--seconds", "-d", help="Length in seconds (up to 240)"),
    instrumental: bool = typer.Option(False, "--instrumental", help="No vocals"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the song, then play it"),
    no_play: bool = typer.Option(False, "--no-play", help="With --wait: do not play it"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate a song (ACE-Step in Audio Foundry)."""
    if not dry_run:
        return _audio.audio_music(style=style, lyrics=lyrics, seconds=seconds,
                                  instrumental=instrumental, wait=wait, no_play=no_play,
                                  server=server, json_out=json_out)
    preview("audio music",
            lambda: _audio.audio_music(style=style, lyrics=lyrics, seconds=seconds,
                                       instrumental=instrumental, wait=False, no_play=True,
                                       server=server, json_out=True),
            inputs=("style_prompt", "lyrics"),
            explicit={"style_prompt", "lyrics"}
            | _explicit(duration_s=seconds if seconds != 30.0 else None,
                        instrumental_only=instrumental),
            json_out=json_out,
            notes=("audio generations are not recorded by the backend yet (issue #8), so there is "
                   "nothing to reproduce from afterwards",))


@audio_app.command("sfx")
def audio_sfx(
    prompt: str = typer.Argument(..., help="SFX / ambience description"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate a sound effect."""
    if not dry_run:
        return _audio.audio_sfx(prompt=prompt, server=server, json_out=json_out)
    preview("audio sfx",
            lambda: _audio.audio_sfx(prompt=prompt, server=server, json_out=True),
            inputs=("prompt",),
            explicit={"prompt"},
            json_out=json_out,
            notes=("audio generations are not recorded by the backend yet (issue #8), so there is "
                   "nothing to reproduce from afterwards",))


@audio_app.command("tts")
def audio_tts(
    text: str = typer.Argument(..., help="Text to speak"),
    no_play: bool = typer.Option(False, "--no-play"),
    dry_run: bool = _DRY,
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Generate speech from text (Audio Foundry, falls back to /api/voice)."""
    if not dry_run:
        return _audio.audio_tts(text=text, no_play=no_play, server=server, json_out=json_out)
    preview("audio tts",
            lambda: _audio.audio_tts(text=text, no_play=True, server=server, json_out=True),
            inputs=("text",),
            explicit={"text"},
            json_out=json_out)
