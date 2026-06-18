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
    probe_duration,
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
    # i2v model selection. Prefer explicit "i2v_model" (e.g. "wan22-14b-i2v") for full
    # flexibility like the main VideoGeneratorPage. Falls back to the legacy i2v_engine
    # mapping for backward compat.
    # Wan 2.2 I2V is generally the highest quality motion option available for the
    # storyboard → i2v flow.
    if not s.get("i2v_model"):
        engine = s.get("i2v_engine", "wan")
        s["i2v_model"] = "wan22-14b-i2v" if engine == "wan" else "cogvideox-5b-i2v"
    s.setdefault("i2v_engine", "wan")  # keep for _max_clip_s etc.
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
    # style_recipe_name: controls global filter/transition palettes for the final Shotcut edit
    # (e.g. "Music Video", "Cinematic", "Grunge" from data/agent/style_recipes). Lets the
    # planner and assembler use appropriate editing tools.
    s.setdefault("style_recipe_name", "default")
    # Director: per-cut distinct prompts (the storyboard layer). ON by default; set
    # False to fall back to one global style_prompt for every clip (the old behavior).
    s.setdefault("director_enabled", True)
    # planning_mode: "narrative" (default — continuity + subjects) or "visual" / "mood_arc"
    # (abstract, energy-driven visual progression / tone poem, better for instrumental / soundtrack / thinking music).
    s.setdefault("planning_mode", "narrative")
    # Optional free-text guidance that was provided at regen time (or at create) and fed
    # to the Director as extra instructions for the mood arc / specific direction.
    s.setdefault("director_guidance", None)
    # Flux asset overrides for keyframe/storyboard still generation (flux-schnell path).
    # Passed to ComfyUIImageGenerator so per-MV choice of GGUF/clip/vae files is possible.
    for k in ("flux_unet", "flux_t5", "flux_clip", "flux_vae"):
        s.setdefault(k, None)
    return s


def _max_clip_s(s: dict) -> float:
    """Longest real forward clip the chosen i2v engine produces, in seconds.

    Derived from the frame clamp in _generate_one_clip: WAN ≤49 frames @24fps,
    CogVideoX ≤25 frames @7fps. The planner uses this × max_stretch as its cut
    ceiling so a forward clip can always fill its slot without a reverse."""
    return (49 / 24) if s.get("i2v_engine", "wan") == "wan" else (25 / 7)


# Fraction of the full Clip Stretch budget applied to the *shortest* (highest-energy) cuts.
# The planner targets cut length in [native_clip × stretch × MIN_STRETCH_FRACTION,
# native_clip × stretch] (loud → short end, calm → long end). At fraction 0.5 and stretch 4,
# loud cuts ≈ 2.04×2 = 4.1s and calm cuts ≈ 8.2s; at stretch 1 the whole band collapses to the
# native ~2s clip (no slow-mo). Tunable: raise toward 1.0 for more uniform (longer) loud cuts.
MIN_STRETCH_FRACTION = 0.5


def _cut_length_bounds(s: dict) -> tuple[float, float]:
    """(min_cut_s, max_cut_s) the planner targets, both scaled by the Clip Stretch budget so
    the setting actually controls clip length. See MIN_STRETCH_FRACTION."""
    native = _max_clip_s(s)
    stretch = float(s["max_stretch"])
    max_cut_s = native * stretch
    min_cut_s = native * max(1.0, stretch * MIN_STRETCH_FRACTION)
    return min_cut_s, max_cut_s


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


def _keyframe_loras_and_prompt(mv: MusicVideo, s: dict, base_prompt: str) -> tuple[list[str], str]:
    """Resolve the trained LoRA path(s) for the music-video keyframe and prepend
    each Subject's trigger word to the prompt — mirroring Film Crew's
    production_swarm_tasks._shot_loras_and_prompt. A LoRA only locks identity
    when the token it was trained on is actually present at inference, so the
    trigger words go INTO the prompt, not just the LoraLoader chain.

    Honest source-of-truth note: the MusicVideo model has NO cast/subject join
    table (unlike Production → ProductionSubject), and the create form sends only
    a `use_lora_consistency` boolean — no subject picker. So the ONLY place a
    trained-LoRA reference can come from today is settings_json. We honor, in
    priority order, whatever the settings actually carry:
      1. explicit on-disk paths in settings `loras` / `lora_paths` (list[str]);
      2. `subject_ids` (list[int]) → Subject.lora_path + trigger_word — this is
         the seam a future MV cast picker would write into, and resolving it now
         means that picker works with zero further changes here.
    Returns ([], base_prompt) — i.e. a NO-OP — when LoRA consistency is off or no
    reference is reachable, so non-cast videos behave exactly as before.
    """
    if not s.get("use_lora_consistency"):
        return [], base_prompt

    lora_paths: list[str] = []
    triggers: list[str] = []

    # (1) explicit paths in settings (accept either key name).
    explicit = s.get("loras") or s.get("lora_paths") or []
    if isinstance(explicit, (list, tuple)):
        for p in explicit:
            if isinstance(p, str) and p.strip():
                lora_paths.append(p.strip())

    # (2) subject_ids → trained Subject LoRAs (the cast seam, mirrors Film Crew).
    subject_ids = s.get("subject_ids") or []
    if isinstance(subject_ids, (list, tuple)) and subject_ids:
        from backend.models import Subject
        for sid in subject_ids:
            subj = db.session.get(Subject, sid)
            if subj and subj.lora_path:
                lora_paths.append(subj.lora_path)
                triggers.append((subj.trigger_word or subj.name or "").strip())

    # De-dupe paths while preserving order.
    seen: set[str] = set()
    lora_paths = [p for p in lora_paths if not (p in seen or seen.add(p))]
    triggers = [t for t in triggers if t]

    if not lora_paths:
        log.info(
            "music_video %s: use_lora_consistency is ON but no trained LoRA "
            "reference was reachable from settings (no loras/lora_paths and no "
            "subject_ids → Subject.lora_path) — keyframe will render off-model.",
            mv.id,
        )
        return [], base_prompt

    prompt = base_prompt
    if triggers:
        prompt = f"{', '.join(triggers)}, {base_prompt}"
    log.info("music_video %s keyframe: applying %d LoRA(s) %s",
             mv.id, len(lora_paths), [os.path.basename(p) for p in lora_paths])
    return lora_paths, prompt


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
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "analyzing")
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
        # Both bounds scale with Clip Stretch so the setting lengthens clips (energy still varies
        # pacing within the band). max_cut_s also remains the forward-clip ceiling for _split_long_cuts.
        min_cut_s, max_cut_s = _cut_length_bounds(s)
        plan = compute_cut_plan(
            structure["beat_times"], structure["sections"], structure["duration_seconds"],
            max_cut_s=max_cut_s, min_cut_s=min_cut_s,
        )
        if not plan:
            raise RuntimeError("cut planner produced no cuts")

        # Bias the cut planner for slow/dreamlike treatments (user's "slow motion" intent and the treatment's "Motion is Slow").
        # This produces fewer, longer cuts so the edit feels dreamy rather than frantic, even if the raw audio energy is high.
        # The max_stretch setting (slowdown) is already used for max_cut_s, but we also dampen energy effect here.
        slow_pace = False
        treatment_text = (s.get("user_treatment") or s.get("director_treatment") or mv.style_prompt or "").lower()
        if any(kw in treatment_text for kw in ["slow", "dreamlike", "gliding", "drifting", "ethereal", "motion is slow", "slow and dreamlike"]):
            slow_pace = True

        if slow_pace:
            # Recompute plan with energy dampened so even "high energy" sections get longer cuts.
            # This respects the artistic intent from the treatment ("Motion is Slow", dreamlike) over the raw librosa energy.
            # Combined with high max_stretch (slow motion setting), this produces the desired long, stretched, atmospheric shots
            # instead of many short frantic ones.
            plan = compute_cut_plan(
                structure["beat_times"], structure["sections"], structure["duration_seconds"],
                max_cut_s=max_cut_s, min_cut_s=min_cut_s,
                slow_pace=True,
            )

        # Director: now generates an actual VISUAL STORYLINE (narrative arc mapped to
        # the song sections + energy) first, then distinct per-cut prompts that advance
        # that storyline. This prevents the "every cut is just the global style repeated"
        # problem. Still degrades gracefully to the old behavior on any LLM failure.
        shot_plans = {}
        if s.get("director_enabled", True):
            from backend.services.music_video_director import _generate_storyline_and_prompts, DIRECTOR_MODEL, _is_embedding_model
            director_model = s.get("director_model") or DIRECTOR_MODEL
            if _is_embedding_model(director_model):
                logging.getLogger(__name__).warning("overriding bad director_model=%s (embedding model cannot chat) -> %s", director_model, DIRECTOR_MODEL)
                director_model = DIRECTOR_MODEL
            result = _generate_storyline_and_prompts(
                mv.style_prompt,
                plan,
                model=director_model,
                planning_mode=s.get("planning_mode", "narrative"),
                extra_guidance=s.get("director_guidance"),
                user_treatment=s.get("user_treatment") or s.get("director_treatment"),
                max_stretch=float(s.get("max_stretch", 2.0)),
                fill_method=s.get("fill_method"),
            )
            # P0 guard application (story-arc plan): ensure the prompts we store for
            # storyboards + i2v are distinct + energy-aware even on marginal LLM output.
            # Also the natural place to (in future) pass stretch context for duration suggestions.
            raw_prompts = result.get("prompts") or []
            guarded = raw_prompts
            try:
                from backend.services.music_video_director import _ensure_distinct_and_energy_aware
                guarded = _ensure_distinct_and_energy_aware(
                    raw_prompts, plan, mv.style_prompt,
                    max_stretch=float(s.get("max_stretch", 2.0)),
                )
            except Exception:  # noqa: BLE001 — guard is best-effort
                pass
            if guarded:
                result["prompts"] = guarded  # feed the guarded list downstream

            # If a rich user-provided treatment was supplied, we can also store the
            # raw treatment the Director produced/refined so the UI can show the
            # final version the agent settled on.
            if result.get("treatment"):
                s = dict(mv.settings_json or {})
                s["director_treatment"] = result.get("treatment")
                mv.settings_json = s
            prompts = result["prompts"]
            treatment = result.get("treatment")
            shot_plans = {s.get("index"): s for s in (result.get("shots") or []) if isinstance(s, dict)}
            if treatment:
                s = dict(mv.settings_json or {})
                s["director_treatment"] = treatment
                mv.settings_json = s
            # Store any director diagnostics (fallback reason, raw head, etc.) so _mv_dict
            # can surface them in the UI for the "why are my prompts not unique?" case.
            if result.get("director_diagnostics"):
                ss = dict(mv.settings_json or {})
                ss["director_diagnostics"] = result.get("director_diagnostics")
                mv.settings_json = ss
        else:
            prompts = [mv.style_prompt] * len(plan)

        # Enrich clips with Director's editing decisions (duration, transition, filter)
        # so the final Shotcut assembly can use real cinematic editing instead of hard-coded hard-cuts.
        mv.song_path = song  # cache the resolved path for later stages
        mv.cut_plan = plan
        mv.clips = []
        for c in plan:
            idx = c["index"]
            sp = shot_plans.get(idx, {})
            # Prefer the unique visual prompt from the detailed shot plan (produced by the Director from the treatment)
            # over the flat prompts list. This ensures we get the per-cut variation the model was instructed to create.
            shot_prompt = sp.get("prompt") or (prompts[idx] if idx < len(prompts) else mv.style_prompt)
            clip = {
                "index": idx,
                "start": c["start_s"],
                "end": c["end_s"],
                "clip_path": None,
                "status": "pending",
                "prompt": shot_prompt,
                "duration_seconds": sp.get("duration_seconds"),
                "transition_to_next": sp.get("transition_to_next"),
                "filter_preset": sp.get("filter_preset"),
            }
            mv.clips.append(clip)
        db.session.commit()
        log.info("music_video %s analyzed: %d cuts over %.1fs (director=%s, has_treatment=%s)",
                 mv_id, len(plan), structure["duration_seconds"], s.get("director_enabled", True),
                 bool(mv.settings_json.get("director_treatment") if isinstance(mv.settings_json, dict) else False))


# --- Stage: generating (self-re-dispatching, one clip per invocation) --------

def run_clip_generator(mv_id: int):
    """Generate ONE pending clip, then tail-call self. When none remain, advance
    generating → assembling and dispatch the assembler.

    Idempotent/crash-safe: a clip counts as done only if status=='done' AND its
    file exists on disk (a half-written file from a crash re-generates)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or mv.current_stage != "generating":
        return

    if (mv.status or "").startswith("cancelled"):
        logger.info(f"Music video {mv_id} is cancelled; stopping clip generation")
        return

    clips = list(mv.clips or [])
    target = None
    for c in clips:
        if c.get("status") == "cancelled":
            continue
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
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "generating")
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
    """(Optional pre-curated storyboard still) → WAN i2v → fill-to-duration for a single cut.

    If the clip has a "storyboard_path" from the earlier "Generate Storyboards" review
    phase (and it exists), we reuse that reviewed keyframe as the i2v init image instead
    of re-generating a fresh still. This supports the thumbnails-first + individual
    regen workflow.

    GPU work (still or i2v) is wrapped in the JobOperationGate's VIDEO_RENDER slot
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
    base_slot_s = float(clip["end"]) - float(clip["start"])
    # Planner (Director) can suggest artistic duration for this shot (longer for drama, shorter for punch).
    # This controls only the *generated motion* length for the i2v call (saves VRAM/compute on long dreamy shots).
    # The *filled output clip file* is always produced at exactly the full timeline slot length so that
    # the MLT assembly (source_out + timeline slots) matches the actual media duration on disk. This
    # contract is required for audio sync and to prevent timing drift or underruns in the .mlt.
    suggested = clip.get("duration_seconds")
    max_src = base_slot_s * float(s.get("max_stretch", 2.0))
    if suggested and 0.5 < float(suggested) <= max_src:
        motion_len_s = float(suggested)
    else:
        # P1: default to a mild stretch target (1.3x) so the final motion after fill
        # feels intentional rather than 1:1 or fully clamped by the i2v engine max.
        ideal = base_slot_s / 1.3
        motion_len_s = max(0.5, min(ideal, max_src, base_slot_s))
    fill_target_s = base_slot_s   # always the full cut slot for the final clip_*.mp4
    out_fps = s["fps"]   # final clip fps (the fill step re-times to this)

    # Engine / model selection. Full model id (wan22-14b-i2v etc.) is now first-class
    # so users can pick via GUI (similar to VideoGeneratorPage). The still (keyframe)
    # is generated first (SDXL path when LoRA consistency is on), then fed to the chosen I2V.
    # Wan 2.2 I2V is preferred for quality when the user has the GPU budget.
    i2v_model = s.get("i2v_model", "wan22-14b-i2v")
    if "wan" in i2v_model.lower():
        i2v_fps = 24
        frames = max(17, min(49, int(round(motion_len_s * i2v_fps)) or 25))
    else:
        i2v_fps = 7
        frames = max(14, min(25, int(round(motion_len_s * i2v_fps)) or 25))

    # If the user has already curated storyboards (via the "thumbnails first" review
    # flow), reuse the reviewed storyboard image as the init for i2v instead of
    # re-generating a fresh still. This honors individual storyboard approvals/regens.
    pregen_storyboard = clip.get("storyboard_path")
    use_pregen_storyboard = bool(pregen_storyboard and os.path.exists(pregen_storyboard))
    if use_pregen_storyboard:
        img = pregen_storyboard
    else:
        img = None  # will be set by still generation below

    # gpu_session = the unified front door: claims the JobOperationGate slot (same
    # fail-fast GpuBusyError + 8s cooldown) and, once we hold it, evicts Ollama so an
    # active chat's resident gemma (~5min keep_alive) can't fight WAN for the 16GB
    # card — the chat engine + training already do this before heavy GPU work; the
    # music-video render previously didn't (documented gap). The mid-session
    # _comfyui_free_vram() below stays explicit (the FLUX→i2v evict is mid-block).
    # vram_estimate_mb makes this keyframe+i2v render visible to the GPU orchestrator's
    # budget so it evicts competing in-process models instead of both fighting for 16GB.
    with gpu_session(JobKind.VIDEO_RENDER, f"mv_{mv.id}_{idx}", evict_ollama=True,
                     vram_estimate_mb=14000):
        if not use_pregen_storyboard:
            # Keyframe (storyboard still) generation.
            # When use_lora_consistency is true (or loras are present), the SDXL+LoRA path
            # in ComfyUIImageGenerator is required for identity. When false, we can in the
            # future swap to FLUX or other high-aesthetic models for prettier keyframes
            # before feeding to the chosen I2V (Wan2.2 I2V etc.).
            kf_steps = s.get("keyframe_steps") or 30
            # Thread the trained character/style LoRA(s) into the SDXL keyframe so
            # identity actually shows up in the still that the i2v then animates
            # (mirrors Film Crew's storyboard_artist). No-op unless LoRA
            # consistency is on AND a LoRA reference is reachable from settings.
            kf_loras, kf_prompt = _keyframe_loras_and_prompt(mv, s, clip_prompt)
            # Per media/vram team audit (resumed): allow per-clip keyframe LoRA strength (default 0.25
            # matches ComfyUIImageGenerator; higher can "fry" identity, lower is safer for
            # consistency). Source from settings if operator tuned it for this MV.
            # Preflight + clamp now wired in api paths + generator too (was the noted WIP).
            kf_lora_strength = max(0.0, min(1.5, float(s.get("keyframe_lora_strength", 0.25))))
            vg = get_video_generator()
            if not getattr(vg, "service_available", True):
                try:
                    vg.service_available = vg._check_comfyui_connection()
                except Exception:
                    vg.service_available = False
            if not getattr(vg, "service_available", True):
                raise RuntimeError("ComfyUI unavailable for music-video keyframe/i2v (start it or free VRAM).")
            img = ComfyUIImageGenerator(
                lora_strength=kf_lora_strength,
                flux_unet=s.get("flux_unet"),
                flux_t5=s.get("flux_t5"),
                flux_clip=s.get("flux_clip"),
                flux_vae=s.get("flux_vae"),
            ).generate_image(
                prompt=kf_prompt, loras=kf_loras, output_path=still_path,
                width=s["still_width"], height=s["still_height"], seed=1000 + idx,
                steps=kf_steps,
                model=s.get("keyframe_model"),
            )
            # Evict FLUX before the animator loads — the i2v nodes don't ask ComfyUI to
            # make room, so without this the animator OOMs on a FLUX-full card.
            _comfyui_free_vram()
        else:
            # Using a user-curated storyboard from the review phase. Still ensure the
            # card is clean before loading the i2v models (same free as the still path).
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
        vg = get_video_generator()
        if not getattr(vg, "service_available", True):
            try:
                vg.service_available = vg._check_comfyui_connection()
            except Exception:
                vg.service_available = False
        if not getattr(vg, "service_available", True):
            raise RuntimeError("ComfyUI unavailable for music-video i2v clip render.")
        result = vg.generate_video(req)
        if not result.success or not result.video_path:
            err = result.error or "no video produced"
            if any(kw in (err or "").lower() for kw in ("oom", "out of memory", "cuda")):
                raise RuntimeError(
                    f"{i2v_model} i2v OOM ({err}). Reduce i2v_steps/resolution, ensure VRAM free "
                    "(Comfy /free), or lower interpolation. See media team audit for preflight."
                )
            raise RuntimeError(f"{i2v_model} i2v failed: {err}")
        wan_abs = resolve_generated_video_path(result, out_dir)
        if not wan_abs.exists():
            raise RuntimeError(f"WAN output not found at resolved path: {wan_abs}")

    # Fill to the EXACT cut length (memory #721 sync fix) — CPU ffmpeg, no gate.
    # method=forward keeps motion forward (no moonwalk); max_stretch caps slowdown.
    # fill_target_s (the full slot) guarantees the written clip_*.mp4 duration matches
    # the source_out + timeline slot the assembler will declare for the .mlt.
    fill_clip_to_duration(
        str(wan_abs), fill_target_s, final_path,
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
    log.info("music_video %s clip %s done (%.2fs)", mv.id, idx, fill_target_s)


# --- Stage: assembling -------------------------------------------------------

def run_assembler(mv_id: int):
    """Compose the filled clips against their exact cut timestamps with the song
    as the audio track; render the final mp4 via the MLT/melt plugin."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv and (mv.status or "").startswith("cancelled"):
        logger.info(f"Music video {mv_id} is cancelled; skipping assemble")
        return

    with _mv_run(mv_id, expected_stage="assembling", next_agent=None) as mv:
        if mv is None:
            return
        from backend.services.plugin_bridge import ensure_plugins_for_stage
        ensure_plugins_for_stage("music-video", "assembling")

        clips = [
            c for c in (mv.clips or [])
            if c.get("status") == "done" and c.get("clip_path") and os.path.exists(c["clip_path"])
        ]
        if not clips:
            raise RuntimeError("no completed clips to assemble")

        s = _settings(mv)
        arrangement_clips = []
        for c in clips:
            # Use the full energy-planned cut length for timeline slot (audio sync).
            # Prefer the *actual* duration of the filled clip file on disk for source_out
            # (defensive against any pre-fix renders or rounding). This ensures the .mlt
            # chains/entries request a play length that the avformat producer can actually deliver.
            base_cut_len = float(c["end"]) - float(c["start"])
            actual_dur = probe_duration(c["clip_path"]) or base_cut_len
            source_out = min(actual_dur, base_cut_len)

            arrangement_clips.append({
                "clip_id": f"mv{mv_id}_{c['index']}",
                "source_path": c["clip_path"],
                "section_label": "",
                "timeline_start": float(c["start"]),
                "timeline_end": float(c["end"]),
                "source_in": 0.0,
                "source_out": source_out,
                "filter_preset": c.get("filter_preset") or "none",
                "transition_to_next": c.get("transition_to_next") or "hard-cut",
            })

        song_duration = mv.cut_plan[-1]["end_s"] if mv.cut_plan else None
        style_recipe = s.get("style_recipe_name", "default")
        body = {
            "arrangement": {"style_recipe_name": style_recipe, "seed": 0, "clips": arrangement_clips},
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

        # Convenience copy: put a nicely-named .mlt next to the music_video's clips/
        # so the user can easily find and open "the shotcut file" for this project
        # without hunting in the opaque mlt-projects/ hash dir. The XML still uses
        # absolute paths to the clips, so it is self-contained for Shotcut.
        try:
            mlt_path = result.get("mlt_path")
            if mlt_path and os.path.exists(mlt_path):
                nice_mlt = _clip_dir(mv_id).parent / f"music_video_{mv_id}.mlt"
                import shutil
                shutil.copy2(mlt_path, nice_mlt)
                log.info("music_video %s: convenience .mlt also at %s", mv_id, nice_mlt)
        except Exception:  # noqa: BLE001
            pass

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
