"""Music-video pipeline Celery tasks.

Clones the Production swarm pattern (production_swarm_tasks.py): a stage-guarded
context manager that no-ops on stage mismatch (idempotent crash-resume), fails
the stage cleanly on any exception, and on clean exit atomically advances +
tail-calls the next agent.

Stages: analyzing → (USER GATE) → generating → assembling → complete.

The generating stage is special: it self-re-dispatches ONE clip per invocation
(see run_clip_generator) so a 100+ clip render never blocks the worker for hours,
crash-resumes per-clip, and lets other queued work interleave between clips.
"""
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import requests
from celery import Celery
from flask import current_app

from backend.models import db, MusicVideo, Document
from backend.services.music_video_service import (
    MusicVideoService,
    compute_cut_plan,
    fill_clip_to_duration,
)
from backend.services.plugin_bridge import ensure_plugin_running, PluginUnavailable

log = logging.getLogger(__name__)

PLUGIN_URL = "http://127.0.0.1:8207"   # video_editor plugin (analyze + assemble)
# Between clips, wait out the GPU gate's post-release cooldown (job_operation_gate
# GPU_RELEASE_COOLDOWN_S ~8s) before the next clip claims the GPU — otherwise the
# tail-call hits "GPU cooling down" immediately. Also the retry delay for transient
# GPU-busy / plugin-cooldown conditions.
GPU_COOLDOWN_RETRY_S = 12


def _settings(mv: MusicVideo) -> dict:
    """Render settings with defaults. Landscape 1080p @24fps (WAN's native fps);
    stills generated at a VRAM-friendly 16:9 and cover-scaled at fill time."""
    s = dict(mv.settings_json or {})
    s.setdefault("fps", 24)
    s.setdefault("width", 1920)
    s.setdefault("height", 1080)
    s.setdefault("still_width", 1024)
    s.setdefault("still_height", 576)
    # i2v RENDER dims — 16:9 landscape (832x480 = WAN's standard 480p, low OOM risk,
    # divisible-by-16). WITHOUT this the request defaults to 512x512 and every clip
    # renders SQUARE, then the fill cover-crops it (the "square video" bug). The
    # fill step cover-scales this to the final width/height (1920x1080), and since
    # it's already 16:9 there's no crop. Bump to 1280x720 for more detail if VRAM allows.
    s.setdefault("i2v_width", 832)
    s.setdefault("i2v_height", 480)
    # i2v engine. DEFAULT "wan" (Wan 2.2 14B Q5 GGUF) — quality-first: it's the only
    # animator that actually fits 16GB (dual high/low-noise model that swaps, ~11GB
    # at a time) AND produces real motion. Higher quality available by pointing at
    # Q6/Q8 quants. "cogvideox" is an opt-in "weak/experimental" path (the on-disk
    # fp8 is a broken 0-byte download and bf16 OOMs at 16GB — so it's not usable
    # here without a working quant). Override per-row via settings_json.i2v_engine.
    s.setdefault("i2v_engine", "wan")
    # --- Playback / cost tuning (per-video; surfaced in the create form) -------
    # fill_method: how a short generated clip is stretched to fill its cut slot.
    #   "forward"   — forward motion only, slow-to-fill (DEFAULT; fixes the moonwalk)
    #   "boomerang" — legacy forward+reverse (the moonwalk; opt-in for ambient clips)
    #   "loop"      — forward repeat
    s.setdefault("fill_method", "forward")
    # max_stretch: per-clip stretch budget. The planner caps each cut at
    # max_clip_s × max_stretch, and the forward fill slows a clip up to this factor.
    # 2.0 = natural slowdown, no clip-halving. Raise it to trade GPU clips for
    # more CPU slowdown (fewer, longer cuts) — the opt-in "render fewer, slow down".
    s.setdefault("max_stretch", 2.0)
    # i2v_steps: override WAN denoising steps (None → engine default 25). The
    # "increase steps a hair" quality lever when slowing clips down more.
    s.setdefault("i2v_steps", None)
    # interpolation_multiplier: RIFE frame interpolation at generation (1=off,
    # 2=double, 4=quad). The "more frames" lever for smooth slow-mo. Default 2
    # preserves the prior implicit behavior (VideoGenerationRequest's own default).
    s.setdefault("interpolation_multiplier", 2)
    # Director: per-cut distinct prompts (the storyboard layer). ON by default; set
    # False to fall back to one global style_prompt for every clip (the old behavior).
    s.setdefault("director_enabled", True)
    return s


def _max_clip_s(s: dict) -> float:
    """Longest real forward clip the chosen i2v engine produces, in seconds.

    Derived from the frame clamp in _generate_one_clip: WAN ≤49 frames @24fps,
    CogVideoX ≤25 frames @7fps. The planner uses this × max_stretch as its cut
    ceiling so a forward clip can always fill its slot without a reverse."""
    return (49 / 24) if s.get("i2v_engine", "wan") == "wan" else (25 / 7)


COMFYUI_URL = "http://127.0.0.1:8188"


def _comfyui_free_vram():
    """Unload ComfyUI's resident models so the next step gets a clean GPU.

    CRITICAL between the FLUX still and the i2v: ComfyUI custom i2v nodes
    (CogVideoXWrapper, and to a lesser degree the WAN GGUF loader) move their
    models onto CUDA WITHOUT asking ComfyUI to evict anything first — so FLUX's
    ~10GB stays resident and the animator's text-encoder/transformer load OOMs
    (observed: CogVideoTextEncode torch.OutOfMemoryError). Freeing here gives the
    animator the full card and lets us run higher-quality (Q6/Q8) quants.

    Delegates to the canonical reclaim in gpu_resource_policy — one implementation
    shared across every image→video handoff. Best-effort — never fatal."""
    from backend.services.gpu_resource_policy import free_comfyui_vram
    free_comfyui_vram()


def _clip_dir(mv_id: int) -> Path:
    try:
        from backend.config import OUTPUT_DIR
    except Exception:
        OUTPUT_DIR = os.path.join(os.getcwd(), "data", "outputs")
    d = Path(OUTPUT_DIR) / "videos" / f"music_video_{mv_id}" / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_song_path(mv: MusicVideo) -> str | None:
    """Absolute on-disk path for the song: cached song_path wins, else resolve the
    Document. Uploaded Documents store a path relative to UPLOAD_DIR (data/uploads),
    not cwd — try upload-relative first, then absolute, then cwd-relative."""
    if mv.song_path and os.path.exists(mv.song_path):
        return mv.song_path
    if mv.song_document_id:
        doc = db.session.get(Document, mv.song_document_id)
        if doc:
            path = getattr(doc, "file_path", None) or doc.path or doc.filename
            if path:
                from backend.config import UPLOAD_DIR
                p = Path(path)
                candidates = [p] if p.is_absolute() else [Path(UPLOAD_DIR) / p, Path.cwd() / p]
                for c in candidates:
                    if c.exists():
                        return str(c.resolve())
    return None


@contextmanager
def _mv_run(mv_id: int, *, expected_stage: str, next_agent: str | None):
    """Stage guard + auto-advance, mirroring production_swarm_tasks._agent_run.

    No-ops if the row isn't at expected_stage (idempotent re-dispatch). On any
    exception, fail the stage and ABSORB (Celery's default retry would otherwise
    loop). On clean exit, atomically advance and tail-call next_agent.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or mv.current_stage != expected_stage:
        yield None
        return
    try:
        yield mv
    except Exception as e:  # noqa: BLE001
        log.exception("music_video stage '%s' failed for %s", expected_stage, mv_id)
        MusicVideoService(db.session).fail_stage(mv_id, stage=expected_stage, error=str(e))
    else:
        advanced = MusicVideoService(db.session).advance_if_predecessor(
            mv_id, expected_predecessor=expected_stage
        )
        if advanced and next_agent:
            from backend.celery_app import celery
            celery.send_task(f"music_video.run_{next_agent}", args=[mv_id])


# --- Stage: analyzing --------------------------------------------------------

def run_analyzer(mv_id: int):
    """Analyze the song → energy-aware cut plan → seed the per-clip cursor.

    next_agent=None: on success we advance analyzing → awaiting_approval, which is
    the USER COST GATE. Generation is dispatched only after the user approves
    (see music_video_api approve), never automatically.
    """
    with _mv_run(mv_id, expected_stage="analyzing", next_agent=None) as mv:
        if mv is None:
            return
        ensure_plugin_running("video_editor")
        song = _resolve_song_path(mv)
        if not song:
            raise RuntimeError("song file not found on disk")

        resp = requests.post(
            f"{PLUGIN_URL}/analyze",
            json={"audio_path": song, "section_count": 4},
            timeout=120,
        )
        resp.raise_for_status()
        structure = resp.json()

        # Cap cut length at what a forward clip can fill (clip native length ×
        # the per-video stretch budget) so no slot needs a reverse to cover it.
        s = _settings(mv)
        max_cut_s = _max_clip_s(s) * float(s["max_stretch"])
        plan = compute_cut_plan(
            structure["beat_times"], structure["sections"], structure["duration_seconds"],
            max_cut_s=max_cut_s,
        )
        if not plan:
            raise RuntimeError("cut planner produced no cuts")

        # Director: a DISTINCT, narratively-connected visual prompt per cut (instead
        # of reusing one global style for every clip). Runs here — pre-approval, no
        # GPU — and degrades to the global style on any failure (no regression).
        if s.get("director_enabled", True):
            from backend.services.music_video_director import generate_scene_prompts
            prompts = generate_scene_prompts(mv.style_prompt, plan)
        else:
            prompts = [mv.style_prompt] * len(plan)

        mv.song_path = song  # cache the resolved path for later stages
        mv.cut_plan = plan
        mv.clips = [
            {"index": c["index"], "start": c["start_s"], "end": c["end_s"],
             "clip_path": None, "status": "pending", "prompt": prompts[c["index"]]}
            for c in plan
        ]
        db.session.commit()
        log.info("music_video %s analyzed: %d cuts over %.1fs (director=%s)",
                 mv_id, len(plan), structure["duration_seconds"], s.get("director_enabled", True))


# --- Stage: generating (self-re-dispatching, one clip per invocation) --------

def run_clip_generator(mv_id: int):
    """Generate ONE pending clip, then tail-call self. When none remain, advance
    generating → assembling and dispatch the assembler.

    Idempotent/crash-safe: a clip counts as done only if status=='done' AND its
    file exists on disk (a half-written file from a crash re-generates)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or mv.current_stage != "generating":
        return

    clips = list(mv.clips or [])
    target = None
    for c in clips:
        on_disk = c.get("clip_path") and os.path.exists(c["clip_path"])
        if not (c.get("status") == "done" and on_disk):
            target = c
            break

    if target is None:
        # All clips done → advance + dispatch assembler (atomic; race-safe).
        svc = MusicVideoService(db.session)
        if svc.advance_if_predecessor(mv_id, expected_predecessor="generating"):
            from backend.celery_app import celery
            celery.send_task("music_video.run_assembler", args=[mv_id])
        return

    from backend.celery_app import celery
    from backend.services.job_operation_gate import GpuBusyError
    try:
        ensure_plugin_running("comfyui")
        _generate_one_clip(mv, target)
    except (GpuBusyError, PluginUnavailable) as e:
        # TRANSIENT — the GPU gate is cooling down / busy, or the plugin is still
        # coming up. Do NOT fail the stage; re-dispatch this same clip after the
        # cooldown clears. The clip is still pending, so we resume exactly here.
        log.info("music_video %s clip %s deferred (transient): %s", mv_id, target.get("index"), e)
        celery.send_task("music_video.run_clip_generator", args=[mv_id], countdown=GPU_COOLDOWN_RETRY_S)
        return
    except Exception as e:  # noqa: BLE001
        log.exception("music_video %s clip %s generation failed", mv_id, target.get("index"))
        MusicVideoService(db.session).fail_stage(mv_id, stage="generating", error=str(e))
        return

    # Continue with the next clip — but AFTER the GPU gate's release cooldown, so
    # the next clip doesn't immediately trip "GPU cooling down". Re-queues at the
    # back, so other work interleaves between clips rather than starving.
    celery.send_task("music_video.run_clip_generator", args=[mv_id], countdown=GPU_COOLDOWN_RETRY_S)


def _generate_one_clip(mv: MusicVideo, clip: dict):
    """FLUX still → WAN i2v → fill-to-duration for a single cut.

    GPU work (still + i2v) is wrapped in the JobOperationGate's VIDEO_RENDER slot
    so it serializes against training/other renders on the shared card. The ffmpeg
    fill is CPU-only and runs OUTSIDE the gate (don't hold the GPU for ffmpeg).

    We build the VideoGenerationRequest directly (rather than via the
    Wan22I2VGenerator adapter) because this path threads extra knobs the adapter
    doesn't expose — i2v_width/height, num_inference_steps, interpolation_multiplier.
    Result-path resolution (generate_video returns video_path RELATIVE to
    request.output_dir) is shared with the adapters via resolve_generated_video_path;
    we set output_dir to our own clip dir so the base is known."""
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.services.comfyui_video_generator import (
        get_video_generator, VideoGenerationRequest, resolve_generated_video_path,
    )
    from backend.services.gpu_resource_policy import gpu_session
    from backend.services.job_types import JobKind

    s = _settings(mv)
    idx = clip["index"]
    # Per-cut Director prompt (set in run_analyzer); falls back to the global style for
    # rows seeded before the Director existed or when the Director was disabled.
    clip_prompt = clip.get("prompt") or mv.style_prompt
    out_dir = _clip_dir(mv.id)
    still_path = str(out_dir / f"still_{idx}.png")
    final_path = str(out_dir / f"clip_{idx}.mp4")
    target_s = float(clip["end"]) - float(clip["start"])
    out_fps = s["fps"]   # final clip fps (the fill step re-times to this)

    # Engine selection. CogVideoX-5b: 14-25 frames @7fps, no LoRA — faster/lighter.
    # Wan 2.2 14B: 17-49 frames @24fps — higher motion quality, much slower.
    engine = s.get("i2v_engine", "cogvideox")
    if engine == "wan":
        i2v_model, i2v_fps = "wan22-14b-i2v", 24
        frames = max(17, min(49, int(round(target_s * i2v_fps)) or 25))
    else:
        i2v_model, i2v_fps = "cogvideox-5b-i2v", 7
        frames = max(14, min(25, int(round(target_s * i2v_fps)) or 25))

    # gpu_session = the unified front door: claims the JobOperationGate slot (same
    # fail-fast GpuBusyError + 8s cooldown) and, once we hold it, evicts Ollama so an
    # active chat's resident gemma (~5min keep_alive) can't fight WAN for the 16GB
    # card — the chat engine + training already do this before heavy GPU work; the
    # music-video render previously didn't (documented gap). The mid-session
    # _comfyui_free_vram() below stays explicit (the FLUX→i2v evict is mid-block).
    with gpu_session(JobKind.VIDEO_RENDER, f"mv_{mv.id}_{idx}", evict_ollama=True):
        img = ComfyUIImageGenerator().generate_image(
            prompt=clip_prompt, loras=[], output_path=still_path,
            width=s["still_width"], height=s["still_height"], seed=1000 + idx,
        )
        # Evict FLUX before the animator loads — the i2v nodes don't ask ComfyUI to
        # make room, so without this the animator OOMs on a FLUX-full card.
        _comfyui_free_vram()
        req_kwargs = dict(
            model=i2v_model,
            prompt=clip_prompt,
            duration_frames=frames,
            fps=i2v_fps,
            width=s["i2v_width"],                    # 16:9 — else WAN renders 512x512 square
            height=s["i2v_height"],
            enhance_prompt=False,
            output_dir=out_dir,                      # known base → resolvable result
            metadata={"image_path": img},
            # RIFE interpolation — more source frames for smooth slow-mo at fill.
            interpolation_multiplier=int(s["interpolation_multiplier"]),
        )
        # Only override denoising steps when the operator set them (else the
        # request's own default stands — don't silently change current behavior).
        if s.get("i2v_steps"):
            req_kwargs["num_inference_steps"] = int(s["i2v_steps"])
        req = VideoGenerationRequest(**req_kwargs)
        result = get_video_generator().generate_video(req)
        if not result.success or not result.video_path:
            raise RuntimeError(f"{i2v_model} i2v failed: {result.error or 'no video produced'}")
        wan_abs = resolve_generated_video_path(result, out_dir)
        if not wan_abs.exists():
            raise RuntimeError(f"WAN output not found at resolved path: {wan_abs}")

    # Fill to the EXACT cut length (memory #721 sync fix) — CPU ffmpeg, no gate.
    # method=forward keeps motion forward (no moonwalk); max_stretch caps slowdown.
    fill_clip_to_duration(
        str(wan_abs), target_s, final_path,
        fps=out_fps, width=s["width"], height=s["height"],
        method=s["fill_method"], max_stretch=float(s["max_stretch"]),
    )

    # Persist cursor. DEEP copy then reassign: a shallow list copy shares the
    # dict objects with the stored attribute, so mutating-then-reassigning leaves
    # old == new and SQLAlchemy's JSON column flushes NOTHING (the cursor update
    # would be silently lost — and the clip would regenerate forever). deepcopy
    # makes the new value genuinely differ from the stored one.
    import copy
    clips = copy.deepcopy(mv.clips or [])
    for c in clips:
        if c["index"] == idx:
            c["clip_path"] = final_path
            c["status"] = "done"
            break
    mv.clips = clips
    db.session.commit()
    log.info("music_video %s clip %s done (%.2fs)", mv.id, idx, target_s)


# --- Stage: assembling -------------------------------------------------------

def run_assembler(mv_id: int):
    """Compose the filled clips against their exact cut timestamps with the song
    as the audio track; render the final mp4 via the MLT/melt plugin."""
    with _mv_run(mv_id, expected_stage="assembling", next_agent=None) as mv:
        if mv is None:
            return
        ensure_plugin_running("video_editor")

        clips = [
            c for c in (mv.clips or [])
            if c.get("status") == "done" and c.get("clip_path") and os.path.exists(c["clip_path"])
        ]
        if not clips:
            raise RuntimeError("no completed clips to assemble")

        s = _settings(mv)
        arrangement_clips = []
        for c in clips:
            # source_out = the planned cut length: fill_clip_to_duration made the
            # clip exactly this long, so timeline slot == source length == no blank
            # gap (the obs-#721 sync fix realized at the assembly contract).
            cut_len = float(c["end"]) - float(c["start"])
            arrangement_clips.append({
                "clip_id": f"mv{mv_id}_{c['index']}",
                "source_path": c["clip_path"],
                "section_label": "",
                "timeline_start": float(c["start"]),
                "timeline_end": float(c["end"]),
                "source_in": 0.0,
                "source_out": cut_len,
                "filter_preset": "none",
                "transition_to_next": "hard-cut",
            })

        song_duration = mv.cut_plan[-1]["end_s"] if mv.cut_plan else None
        body = {
            "arrangement": {"style_recipe_name": "default", "seed": 0, "clips": arrangement_clips},
            "audio_path": mv.song_path,
            "audio_volume": 1.0,
            "song_duration_seconds": song_duration,
            "fps_num": s["fps"], "fps_den": 1,
            "width": s["width"], "height": s["height"],
            "render_mp4": True, "register": True,
        }
        resp = requests.post(f"{PLUGIN_URL}/shotcut/compose-arrangement", json=body, timeout=1800)
        resp.raise_for_status()
        result = resp.json()

        # compose-arrangement registers BOTH the .mlt project AND the rendered .mp4
        # as Documents. Pick the .mp4 — docs[0] is often the .mlt, which made the
        # in-page <video> player point at a timeline file it can't play.
        docs = [d for d in (result.get("documents") or []) if isinstance(d, dict)]
        def _is_mp4(d):
            return str(d.get("path") or d.get("file_path") or d.get("filename") or "").lower().endswith(".mp4")
        mp4_doc = next((d for d in docs if _is_mp4(d)), None) or (docs[0] if docs else None)
        if mp4_doc:
            mv.output_document_id = mp4_doc.get("id")
        db.session.commit()
        log.info("music_video %s assembled → %s (doc %s)",
                 mv_id, result.get("rendered_mp4"), mv.output_document_id)


# --- Celery factory ----------------------------------------------------------

def create_music_video_tasks(celery_app: Celery):
    @celery_app.task(name="music_video.run_analyzer")
    def run_analyzer_task(mv_id: int):
        with current_app.app_context():
            run_analyzer(mv_id)

    @celery_app.task(name="music_video.run_clip_generator")
    def run_clip_generator_task(mv_id: int):
        with current_app.app_context():
            run_clip_generator(mv_id)

    @celery_app.task(name="music_video.run_assembler")
    def run_assembler_task(mv_id: int):
        with current_app.app_context():
            run_assembler(mv_id)

    return {
        "run_analyzer": run_analyzer_task,
        "run_clip_generator": run_clip_generator_task,
        "run_assembler": run_assembler_task,
    }
