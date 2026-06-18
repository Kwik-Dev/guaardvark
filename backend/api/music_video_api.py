"""Music-video pipeline REST API. Drive the MusicVideo state machine.

Front door for: create (→ analyze), inspect (stage + cut/clip counts + the GPU
cost estimate shown before approval), and the approval gate that releases the
expensive per-clip generation.
"""
import logging
import os
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file

from backend.models import db, MusicVideo, Project, Document
from backend.services.music_video_service import MusicVideoService
from backend.services.gpu_resource_policy import gpu_session, free_comfyui_vram
from backend.services.job_types import JobKind
from backend.services.job_operation_gate import GpuBusyError
from backend.services.plugin_bridge import ensure_plugin_running
from backend.services.comfyui_video_generator import get_video_generator
from backend.services.music_video_director import _is_embedding_model

bp = Blueprint("music_video_api", __name__, url_prefix="/api/music-video")
log = logging.getLogger(__name__)

# Rough per-clip wall-clock for the approval-gate estimate: FLUX still (~20s) +
# WAN i2v (~45s) + gate cooldown (~8s) + ffmpeg fill (~2s). Display-only.
_SECONDS_PER_CLIP = 75


def _resolve_song(song_document_id) -> str | None:
    """Absolute on-disk path for a song Document id, or None if unresolvable.

    Uploaded Documents store a path RELATIVE TO UPLOAD_DIR (data/uploads), not
    cwd — same as backend/utils/uploaded_file_resolver. Try the upload-relative
    location first, then absolute, then cwd-relative as a fallback.
    """
    if not song_document_id:
        return None
    doc = db.session.get(Document, song_document_id)
    if not doc:
        return None
    path = getattr(doc, "file_path", None) or doc.path or doc.filename
    if not path:
        return None
    from backend.config import UPLOAD_DIR
    p = Path(path)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path(UPLOAD_DIR) / p)
        candidates.append(Path.cwd() / p)
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    return None


def _mv_dict(mv: MusicVideo) -> dict:
    cut_count = len(mv.cut_plan or [])
    clips = mv.clips or []
    clips_done = sum(1 for c in clips if c.get("status") == "done")
    out = {
        "id": mv.id,
        "name": mv.name,
        "status": mv.status,
        "current_stage": mv.current_stage,
        "project_id": mv.project_id,
        "song_document_id": mv.song_document_id,
        "style_prompt": mv.style_prompt,
        "cut_count": cut_count,
        "clip_count": len(clips),
        "clips_done": clips_done,
        "output_document_id": mv.output_document_id,
        "error_blob": mv.error_blob,
        "created_at": mv.created_at.isoformat() if mv.created_at else None,
        # Full plan data for the UI (cut list + per-cut prompts from the Director).
        # Only present after the analyzer has run. Safe to send for both list and detail;
        # the sidebar list ignores these fields.
        "cut_plan": mv.cut_plan or [],
        "clips": clips,
    }
    # Surface the cost estimate once the plan exists (i.e. at the approval gate).
    if cut_count:
        est = cut_count * _SECONDS_PER_CLIP
        out["estimate"] = {
            "clips_to_generate": cut_count,
            "seconds_per_clip": _SECONDS_PER_CLIP,
            "estimated_seconds": est,
            "estimated_human": f"~{est // 3600}h {(est % 3600) // 60}m" if est >= 3600 else f"~{est // 60}m",
        }
    # Also surface pipeline / model settings so the UI (Plan viewer) can show exactly what will be / was used.
    s = mv.settings_json or {}
    out["director_enabled"] = s.get("director_enabled", True)
    if s.get("planning_mode"):
        out["planning_mode"] = s.get("planning_mode")
    # Dedicated (usually small/fast) model used for the Director when generating per-cut prompts + treatment.
    # Defaults to a lightweight gemma suitable for structured JSON + visual storytelling (not necessarily the user's main chat/brain model).
    dm = s.get("director_model") or "gemma4:e4b"
    if _is_embedding_model(dm):  # avoid showing bad persisted values in UI
        dm = "gemma4:e4b"
    out["director_model"] = dm
    out["use_lora_consistency"] = s.get("use_lora_consistency", False)
    out["keyframe_model"] = s.get("keyframe_model", "flux-schnell")
    out["i2v_model"] = s.get("i2v_model") or ("wan22-14b-i2v" if s.get("i2v_engine", "wan") == "wan" else "cogvideox-5b-i2v")
    # The rich visual treatment / short story the Director (acting as screenwriter) invented.
    # This is the creative foundation — per-cut prompts should advance this treatment.
    # This is the main defense against "every scene uses the identical repeated global prompt".
    treatment = s.get("director_treatment") or s.get("director_storyline")
    if treatment:
        out["director_treatment"] = treatment
    # P0 diagnostics (story-arc plan): surface why we may have fallen back to global/identical
    # prompts (LLM unavailable, parse failure, recovery used, low distinctness after guard, etc.).
    # The UI (PlanViewer) can show a warning badge so the operator knows the arc is not fully active.
    if isinstance(s, dict) and s.get("director_diagnostics"):
        out["director_diagnostics"] = s.get("director_diagnostics")
    return out


@bp.post("")
def create():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    song_document_id = body.get("song_document_id")
    style_prompt = (body.get("style_prompt") or "").strip()
    project_id = body.get("project_id")
    settings = body.get("settings") or {}
    # User-provided treatment / short story takes precedence as the creative source
    if body.get("user_treatment"):
        settings["user_treatment"] = body.get("user_treatment").strip()
        settings["director_treatment"] = body.get("user_treatment").strip()  # seed the visible treatment

    if not name or not style_prompt or not song_document_id:
        return jsonify({"error": "name, song_document_id and style_prompt are required"}), 400

    if project_id is not None and db.session.get(Project, project_id) is None:
        return jsonify({"error": f"project_id {project_id} not found"}), 400

    song_path = _resolve_song(song_document_id)
    if not song_path:
        return jsonify({"error": f"song_document_id {song_document_id} not found on disk"}), 400

    svc = MusicVideoService(db.session)
    mv = svc.create(
        name=name, song_document_id=song_document_id, song_path=song_path,
        style_prompt=style_prompt, project_id=project_id, settings=settings,
    )

    # Kick the pipeline: draft → analyzing, then dispatch the analyzer. A dispatch
    # failure is non-fatal — state moved forward so boot resume_all picks it up.
    if svc.advance_if_predecessor(mv.id, expected_predecessor="draft"):
        try:
            svc.dispatch_agent(mv.id, "analyzer")
        except Exception as e:  # noqa: BLE001
            log.warning(f"Analyzer dispatch failed for music_video {mv.id}: {e}")
        db.session.refresh(mv)

    return jsonify(_mv_dict(mv)), 201


@bp.get("")
def list_music_videos():
    rows = MusicVideo.query.order_by(MusicVideo.created_at.desc()).all()
    return jsonify({"music_videos": [_mv_dict(mv) for mv in rows]})


@bp.get("/<int:mv_id>")
def get_music_video(mv_id):
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_mv_dict(mv))


@bp.delete("/<int:mv_id>")
def delete_music_video(mv_id):
    """Clear one music video from the log.

    Removes the MusicVideo row only — the rendered output Document (if any) and
    any uploaded song are left intact; this just clears the generation entry the
    operator sees in the list.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(mv)
    db.session.commit()
    return jsonify({"deleted": mv_id})


@bp.delete("")
def clear_music_videos():
    """Bulk-clear music videos from the log.

    Default: only terminal entries (``complete`` or ``failed*``) — the safe
    "clear finished" sweep. Pass ``?all=true`` to clear every entry regardless
    of stage (still leaves output Documents on disk).
    """
    clear_all = (request.args.get("all") or "").lower() in ("1", "true", "yes")
    rows = MusicVideo.query.all()

    def _is_terminal(mv: MusicVideo) -> bool:
        status = (mv.status or "")
        return (
            mv.current_stage == "complete"
            or status.startswith("failed")
            or status.startswith("cancelled")
            or mv.current_stage == "cancelled"
        )

    targets = rows if clear_all else [mv for mv in rows if _is_terminal(mv)]
    deleted = [mv.id for mv in targets]
    for mv in targets:
        db.session.delete(mv)
    db.session.commit()
    return jsonify({"deleted": deleted, "count": len(deleted)})


@bp.post("/<int:mv_id>/approve")
def approve(mv_id):
    """Cost gate: release per-clip generation only on explicit operator approval."""
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"music_video is at stage '{mv.current_stage}', not awaiting_approval"
        }), 409

    # LoRA/Comfy preflight+clamp resumed (P1-5/P3-12 media items from team audit; was WIP
    # after phase-map/flux wiring). Now consistent with clip path + generator helper.
    try:
        vg = get_video_generator()
        if not getattr(vg, "service_available", True):
            vg.service_available = vg._check_comfyui_connection() if hasattr(vg, "_check_comfyui_connection") else False
        if not getattr(vg, "service_available", True):
            return jsonify({"error": "ComfyUI not reachable; cannot release music-video generation. Start ComfyUI."}), 503
    except Exception as _pf:
        log.warning(f"Music-video preflight soft-failed (proceeding): {_pf}")

    svc = MusicVideoService(db.session)
    if svc.advance_if_predecessor(mv_id, expected_predecessor="awaiting_approval"):
        try:
            svc.dispatch_agent(mv_id, "clip_generator")
        except Exception as e:  # noqa: BLE001
            log.warning(f"Clip generator dispatch failed for music_video {mv_id}: {e}")
        db.session.refresh(mv)

    return jsonify(_mv_dict(mv))


# --- Pre-approval plan inspection & editing -------------------------------

@bp.post("/<int:mv_id>/plan")
def update_plan(mv_id):
    """Accept operator edits to the per-cut prompts (and optionally global style).

    Body: { "prompts": { "<index>": "new visual prompt text", ... }, "style_prompt"?: "..." }
    Only allowed while the video is parked at awaiting_approval (before any GPU work).
    The edited prompts will be used when generation starts.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"plan edits only allowed at 'awaiting_approval', currently '{mv.current_stage}'"
        }), 409

    body = request.get_json(silent=True) or {}
    svc = MusicVideoService(db.session)

    # Optional global style tweak
    new_style = body.get("style_prompt")
    if isinstance(new_style, str) and new_style.strip():
        mv.style_prompt = new_style.strip()

    # Per-clip prompt patches: support both {prompts: {0: ".." }} and {clips: [{index, prompt}, ...]}
    prompt_updates: dict[int, str] = {}
    if isinstance(body.get("prompts"), dict):
        for k, v in body["prompts"].items():
            try:
                prompt_updates[int(k)] = str(v or "")
            except (ValueError, TypeError):
                pass
    if isinstance(body.get("clips"), list):
        for item in body["clips"]:
            if isinstance(item, dict):
                try:
                    idx = int(item.get("index"))
                    prompt_updates[idx] = str(item.get("prompt") or "")
                except (ValueError, TypeError):
                    pass

    updated = svc.update_clip_prompts(mv_id, prompt_updates)

    # Support editing the treatment / story directly (user pasted or refined the creative treatment)
    if "treatment" in body and isinstance(body["treatment"], str):
        s = dict(mv.settings_json or {})
        s["user_treatment"] = body["treatment"].strip()
        # Also update the active director_treatment so the plan shows the user version
        s["director_treatment"] = body["treatment"].strip()
        mv.settings_json = s

    if updated is None:
        # Shouldn't happen because we checked stage above, but be defensive.
        updated = mv
    db.session.refresh(updated)
    return jsonify(_mv_dict(updated))


@bp.post("/<int:mv_id>/replan")
def replan(mv_id):
    """Reset a terminal music video (complete or failed) back to awaiting_approval
    with its existing plan, treatment, and settings intact. This lets the user
    re-edit the treatment/prompts or just re-render (e.g. different seeds or model choices)
    without losing the creative work.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if not (mv.current_stage in ("complete",) or (mv.status or "").startswith("failed")):
        return jsonify({
            "error": f"replan only allowed on complete or failed videos, current stage is '{mv.current_stage}'"
        }), 409

    # Reset generation state but keep the plan + treatment + settings
    mv.current_stage = "awaiting_approval"
    mv.status = "awaiting_approval"
    mv.output_document_id = None
    mv.error_blob = None

    # deepcopy: clips is a plain db.JSON column (not MutableList); mutating the
    # same list object in place is not seen as dirty and won't persist on commit.
    import copy
    clips = copy.deepcopy(mv.clips or [])
    for c in clips:
        c["clip_path"] = None
        c["status"] = "pending"
        # Keep storyboard_path if present (for the storyboard-first review flow)
    mv.clips = clips

    db.session.commit()
    return jsonify(_mv_dict(mv))


@bp.post("/<int:mv_id>/cancel")
def cancel_music_video(mv_id):
    """Cancel a music video that is generating, assembling, or still awaiting approval.
    Marks it cancelled so the clip generator loop will stop re-queuing further clips.
    Current clip may finish (i2v is hard to interrupt mid-frame), but no more work after.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404

    cancellable = ("generating", "assembling", "awaiting_approval")
    if mv.current_stage not in cancellable:
        return jsonify({"error": f"Cannot cancel at stage '{mv.current_stage}'"}), 409

    mv.status = "cancelled"
    # Also advance the stage to a terminal "cancelled" value so UI (which keys
    # heavily on current_stage for progress/labels/active filters) and resume
    # logic immediately see it as done. The per-clip guards already short-circuit
    # on status.startswith("cancelled"), but making the stage explicit prevents
    # "still shows as generating" after cancel and stops resume dispatches.
    mv.current_stage = "cancelled"
    # deepcopy so the reassignment is a new object SQLAlchemy detects as dirty —
    # clips is a plain db.JSON column (not MutableList), so an in-place mutation
    # of the same list is NOT persisted and would silently revert on commit.
    import copy
    clips = copy.deepcopy(mv.clips or [])
    for c in clips:
        if c.get("status") == "pending":
            c["status"] = "cancelled"
    mv.clips = clips
    db.session.commit()

    log.info(f"Music video {mv_id} cancelled by user at stage {mv.current_stage}")
    return jsonify(_mv_dict(mv))


@bp.get("/<int:mv_id>/storyboard/<int:idx>")
def get_mv_storyboard(mv_id, idx):
    """Serve a generated storyboard still for a cut (used in the plan review UI for the storyboard-first flow)."""
    mv = db.session.get(MusicVideo, mv_id)
    if not mv or not mv.clips:
        return jsonify({"error": "not found"}), 404
    for c in (mv.clips or []):
        if int(c.get("index", -1)) == int(idx):
            sp = c.get("storyboard_path")
            if sp and os.path.exists(sp):
                return send_file(sp, mimetype="image/png")
    return jsonify({"error": "storyboard not found for this cut"}), 404


@bp.post("/<int:mv_id>/generate-storyboards")
def generate_storyboards(mv_id):
    """Generate (or re-generate) the storyboard keyframes for all cuts using the configured keyframe model.
    This is the "thumbnails first" step: produces the stills for review/individual regen before the expensive i2v.
    Only allowed while the plan is approved but video not yet started.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({"error": f"storyboard generation only from awaiting_approval (plan approved), current={mv.current_stage}"}), 409

    from backend.services.plugin_bridge import ensure_plugins_for_stage
    ensure_plugins_for_stage("music-video", "storyboard")

    # Signal the GPU/memory orchestrator that we need image-gen capacity
    # (SD/FLUX pipeline). This helps the coordinator/orchestrator track
    # competing demands (Ollama, other image plugins, etc.) and will become
    # the hook for automatic plugin toggling once the full orchestrator logic
    # is wired for music-video storyboard paths.
    try:
        from backend.services.plugin_bridge import prepare_plugins_for_route
        # Use the working prepare path (with persist=False semantics inside ensure for auto).
        # This wires the sub-intent for storyboard-only needs (comfyui phase) without dead method.
        prepare_plugins_for_route("/music-video/storyboard")
    except Exception:
        logger.warning("Failed to prepare plugins for music-video storyboard (non-fatal)", exc_info=True)  # noqa: BLE001

    s = _settings_for_mv(mv)  # small helper below or inline
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.tasks.music_video_tasks import _keyframe_loras_and_prompt, _clip_dir
    import copy

    clips = copy.deepcopy(mv.clips or [])
    out_dir = None
    try:
        out_dir = _clip_dir(mv.id)
    except Exception:
        from pathlib import Path
        out_dir = Path("data/outputs/videos") / f"music_video_{mv_id}" / "clips"  # fallback; non-fatal per bare-excepts audit (infra/security)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Media team audit (P1-5/P3-12): use the shared _keyframe helper (resolves explicit
    # loras + subject cast + prepends trigger words) + clamp strength + basic preflight.
    # This threads LoRA identity into storyboard keyframes (flux-schnell/SDXL path) the
    # same way the clip i2v path does. Previously the approval path used a minimal
    # []/prompt version (uncommitted WIP).
    kf_lora_strength = max(0.0, min(1.0, float(s.get("keyframe_lora_strength", 0.25))))
    try:
        vg = get_video_generator()
        if not getattr(vg, "service_available", True):
            vg.service_available = getattr(vg, "_check_comfyui_connection", lambda: False)()
    except Exception:
        pass

    try:
        # vram_estimate_mb makes storyboard gen visible to the GPU orchestrator budget.
        with gpu_session(JobKind.VIDEO_RENDER, f"mv_storyboards_{mv_id}", evict_ollama=True,
                         vram_estimate_mb=10000):
            for c in clips:
                has_existing = bool(c.get("storyboard_path") and os.path.exists(c.get("storyboard_path")))
                if has_existing and not force:
                    continue  # already have one (classic "missing only" behavior)
                prompt = c.get("prompt") or mv.style_prompt
                idx = c["index"]
                still_path = str(out_dir / f"storyboard_{idx}.png")
                try:
                    # Proper LoRA+trigger resolve for cast consistency in review thumbnails.
                    kf_loras, kf_prompt = _keyframe_loras_and_prompt(mv, s, prompt)
                    # Comfy preflight for LoRAs (existence) — fail soft per cut so one bad
                    # LoRA doesn't kill the whole storyboard batch.
                    if kf_loras:
                        from backend.services.comfyui_image_generator import ComfyUIImageGenerator as _CIG
                        _CIG()._preflight_loras(kf_loras)  # best-effort; logs warnings
                    gen = ComfyUIImageGenerator(
                        lora_strength=kf_lora_strength,
                        flux_unet=s.get("flux_unet"),
                        flux_t5=s.get("flux_t5"),
                        flux_clip=s.get("flux_clip"),
                        flux_vae=s.get("flux_vae"),
                    )
                    gen.generate_image(
                        prompt=kf_prompt,
                        loras=kf_loras,
                        output_path=still_path,
                        width=s.get("still_width", 1024),
                        height=s.get("still_height", 576),
                        seed=2000 + idx,
                        steps=s.get("keyframe_steps") or 20,
                        model=s.get("keyframe_model"),
                    )
                    c["storyboard_path"] = still_path
                    c["storyboard_variation"] = None
                except RuntimeError as e:
                    # Workflow produced no image (or other Comfy runtime failure).
                    # Log with prompt_id if present in the message so operator can
                    # find it in ComfyUI UI. Do not set path for this cut.
                    log.error("generate-storyboards ComfyUI failure for mv %s cut %s: %s", mv_id, idx, e)
                except Exception as e:
                    log.warning(f"storyboard still failed for mv {mv_id} cut {idx}: {e}")
            # Clean up ComfyUI resident models (e.g. FLUX) so the subsequent i2v
            # phase has maximum headroom, mirroring the pattern in _generate_one_clip.
            free_comfyui_vram()
    except GpuBusyError as e:
        return jsonify({"error": f"GPU busy, cannot generate storyboards right now: {e}"}), 503

    mv.clips = clips
    db.session.commit()
    return jsonify(_mv_dict(mv))


@bp.post("/<int:mv_id>/regen-storyboard/<int:idx>")
def regen_mv_storyboard(mv_id, idx):
    """Re-generate a single storyboard still (for the review step before full video render).
    Optional body: { "prompt": "override prompt for this cut" }
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None or mv.current_stage != "awaiting_approval":
        return jsonify({"error": "only allowed during plan/storyboard review"}), 409

    from backend.services.plugin_bridge import ensure_plugins_for_stage
    ensure_plugins_for_stage("music-video", "storyboard")

    # Signal the GPU/memory orchestrator (see comment in generate_storyboards).
    try:
        from backend.services.plugin_bridge import prepare_plugins_for_route
        prepare_plugins_for_route("/music-video/storyboard")
    except Exception:
        logger.warning("Failed to prepare plugins for music-video storyboard regen (non-fatal)", exc_info=True)  # noqa: BLE001

    body = request.get_json(silent=True) or {}
    prompt_override = body.get("prompt")

    s = _settings_for_mv(mv)
    from backend.services.comfyui_image_generator import ComfyUIImageGenerator
    from backend.tasks.music_video_tasks import _keyframe_loras_and_prompt, _clip_dir
    import copy

    clips = copy.deepcopy(mv.clips or [])
    target = None
    for c in clips:
        if int(c.get("index", -1)) == int(idx):
            target = c
            break
    if not target:
        return jsonify({"error": "cut not found"}), 404

    prompt = prompt_override or target.get("prompt") or mv.style_prompt
    try:
        out_dir = _clip_dir(mv.id)
    except Exception:
        from pathlib import Path
        out_dir = Path("data/outputs/videos") / f"music_video_{mv_id}" / "clips"  # fallback; non-fatal per bare-excepts audit (infra/security)
        out_dir.mkdir(parents=True, exist_ok=True)

    # Support optional "variation" for different outputs with the same prompt.
    # If provided, we add it to the base seed so the user (or UI) can request
    # variations without editing the prompt text itself.
    variation = body.get("variation", 0) or 0
    try:
        variation = int(variation)
    except (TypeError, ValueError):
        variation = 0

    # Media team audit resume (P1-5/P3-12): shared helper for LoRA+triggers + clamp + preflight
    # on the regen path (was minimal [] version; now consistent with clip gen and the
    # generate-storyboards path we just updated).
    kf_lora_strength = max(0.0, min(1.0, float(s.get("keyframe_lora_strength", 0.25))))
    kf_loras, kf_prompt = _keyframe_loras_and_prompt(mv, s, prompt)
    if kf_loras:
        ComfyUIImageGenerator()._preflight_loras(kf_loras)

    still_path = str(out_dir / f"storyboard_{idx}.png")
    try:
        # vram_estimate_mb (~SDXL still) makes single-storyboard regen visible to the orchestrator budget.
        with gpu_session(JobKind.VIDEO_RENDER, f"mv_storyboard_{mv_id}_{idx}", evict_ollama=True,
                         vram_estimate_mb=10000):
            ComfyUIImageGenerator(
                lora_strength=kf_lora_strength,
                flux_unet=s.get("flux_unet"),
                flux_t5=s.get("flux_t5"),
                flux_clip=s.get("flux_clip"),
                flux_vae=s.get("flux_vae"),
            ).generate_image(
                prompt=kf_prompt,
                loras=kf_loras,
                output_path=still_path,
                width=s.get("still_width", 1024),
                height=s.get("still_height", 576),
                seed=3000 + int(idx) + variation,
                steps=s.get("keyframe_steps") or 20,
                model=s.get("keyframe_model"),
            )
            # Free ComfyUI VRAM after the still so any follow-up video work
            # (or other users) gets a clean card.
            free_comfyui_vram()
            target["storyboard_path"] = still_path
            # Optionally record the variation used for this cut (for transparency / future re-renders)
            target["storyboard_variation"] = variation
    except GpuBusyError as e:
        return jsonify({"error": f"GPU busy, cannot regenerate storyboard right now: {e}"}), 503
    except RuntimeError as e:
        # ComfyUI accepted the job but produced no usable image (workflow error,
        # missing model, node failure, etc.). Return a clean error with the
        # prompt_id so the operator can inspect it directly in the ComfyUI UI
        # (http://127.0.0.1:8188 or the comfyui plugin port) under the prompt history.
        log.error("regen-storyboard ComfyUI failure for mv %s cut %s: %s", mv_id, idx, e)
        # Try to surface any ComfyUI-side error details if the prompt_id is in the message.
        return jsonify({
            "error": "ComfyUI failed to produce a storyboard image for this cut",
            "details": str(e),
            "cut": idx,
            "suggestion": "Open the ComfyUI interface, look up the prompt_id in the queue/history, and check the node that errored. Common causes: workflow expects different models, custom nodes not loaded, or VRAM pressure after plugin toggle."
        }), 422
    except Exception as e:
        log.exception("Unexpected error in regen-storyboard for mv %s cut %s", mv_id, idx)
        return jsonify({"error": f"Storyboard regen failed: {e}"}), 500

    mv.clips = clips
    db.session.commit()
    return jsonify(_mv_dict(mv))


def _settings_for_mv(mv):
    """Lightweight settings read for storyboard gen (mirrors the one in tasks)."""
    s = dict(mv.settings_json or {})
    s.setdefault("still_width", 1024)
    s.setdefault("still_height", 576)
    s.setdefault("keyframe_steps", 20)
    s.setdefault("keyframe_model", "flux-schnell")
    # Flux asset overrides (for storyboard/keyframe flux workflows). None = use
    # GUAARDVARK_FLUX_* env defaults (which match the documented infographic assets).
    for k in ("flux_unet", "flux_t5", "flux_clip", "flux_vae"):
        s.setdefault(k, None)
    return s


@bp.post("/<int:mv_id>/regenerate-plan")
def regenerate_plan(mv_id):
    """Re-run the Director (music_video_director) over the existing cut plan.

    Optional body:
      { "feedback": "free text guidance, e.g. 'abstract mood arc, more light and texture, slow and dreamy intro'",
        "planning_mode": "narrative" | "visual" | "mood_arc" }

    The chosen mode/guidance are persisted in settings_json and the new prompts replace
    the previous ones in the clips list. Only usable before generation.
    """
    mv = db.session.get(MusicVideo, mv_id)
    if mv is None:
        return jsonify({"error": "not_found"}), 404
    if mv.current_stage != "awaiting_approval":
        return jsonify({
            "error": f"regenerate-plan only allowed at 'awaiting_approval', currently '{mv.current_stage}'"
        }), 409

    body = request.get_json(silent=True) or {}
    feedback = body.get("feedback")
    if isinstance(feedback, str):
        feedback = feedback.strip() or None
    mode = body.get("planning_mode")
    if not isinstance(mode, str):
        mode = None
    director_model = body.get("director_model")
    if not isinstance(director_model, str) or not director_model.strip():
        director_model = None

    svc = MusicVideoService(db.session)
    updated = svc.regenerate_director_prompts(mv_id, feedback=feedback, planning_mode=mode, director_model=director_model)
    if updated is None:
        updated = mv
    db.session.refresh(updated)
    return jsonify(_mv_dict(updated))
