"""Cast Library — CRUD over Subjects. Reusable across Productions."""
import logging
import os
from pathlib import Path

from flask import Blueprint, current_app, request, jsonify, send_file
from werkzeug.utils import secure_filename

from backend.models import db, Subject, SubjectSample

bp = Blueprint("cast_library_api", __name__, url_prefix="/api/cast-library")
log = logging.getLogger(__name__)

VALID_KINDS = {"character", "environment", "prop"}

# Repo root (…/backend/api/cast_library_api.py → parents[2]). Ref paths are stored
# relative to the project root; resolving against this instead of the process cwd
# makes preview serving robust no matter where/how the backend was launched.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref_path(p: str) -> str | None:
    """Return an on-disk absolute path for a stored ref image, or None.
    Tries the value as-is (abs or cwd-relative), then relative to the repo root,
    then relative to the data/STORAGE_DIR — covers training-dir refs and cast_refs
    uploads. The resolved path MUST live under one of those roots: ref_image_paths
    can be set via the create-API body, so this is the guard against a crafted
    '../../etc/passwd' reaching send_file."""
    if not p:
        return None
    roots = [_PROJECT_ROOT]
    try:
        storage = current_app.config.get("STORAGE_DIR")  # registered key (NOT "DATA_DIR")
        if storage:
            roots.append(Path(storage).resolve())
    except RuntimeError:
        pass  # outside app context (shouldn't happen on a request, but be safe)
    candidates = [p] + [str(r / p) for r in roots]
    for c in candidates:
        try:
            if not os.path.isfile(c):
                continue
            # Always hand send_file an ABSOLUTE path — Flask raises on a relative one.
            ap = os.path.abspath(c)
            # Containment: the file must sit inside an allowed root, else skip it.
            if any(os.path.commonpath([ap, str(r)]) == str(r) for r in roots):
                return ap
        except (OSError, ValueError):
            continue
    return None

# Standard image formats — anything else is rejected at upload time.
_ALLOWED_REF_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_MAX_REF_BYTES = 25 * 1024 * 1024  # 25 MB per image — generous, but caps runaway uploads.


def _cast_ref_dir(subject_id: int) -> Path:
    """Where reference images for a Subject live on disk. Created lazily."""
    base = Path(current_app.config.get("STORAGE_DIR") or "data")  # registered key (NOT "DATA_DIR")
    target = base / "cast_refs" / str(subject_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _serialize(s: Subject) -> dict:
    return {
        "id": s.id, "kind": s.kind, "name": s.name,
        "description": s.description,
        "ref_image_paths": s.ref_image_paths or [],
        "trigger_word": s.trigger_word,
        "voice_id": s.voice_id,
        "lora_path": s.lora_path,
        "lora_version": s.lora_version,
        "training_status": s.training_status,
        "training_error": s.training_error,  # why the last run failed (UI surfaces it)
        "bible": s.bible,  # the appearance-lock injected per cut; surfaced for UI preview
    }


@bp.get("")
def list_subjects():
    subjects = Subject.query.order_by(Subject.created_at.desc()).all()
    return jsonify({"subjects": [_serialize(s) for s in subjects]})


@bp.get("/subjects/<int:subject_id>")
def get_subject(subject_id: int):
    """Efficient single-subject fetch (replaces the previous client-side list+find
    pattern in productionService.getCastSubject for the detail page).

    Supports ?include=samples to return the full ordered SubjectSample list
    inline, reducing roundtrips for CastMemberPage loads and polling.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    data = {"subject": _serialize(s)}

    # Optional eager samples for the Cast detail page (Generate + Training tabs).
    if request.args.get("include") == "samples":
        samples = (
            SubjectSample.query
            .filter_by(subject_id=subject_id)
            .order_by(SubjectSample.index)
            .all()
        )
        data["samples"] = [r.to_dict() for r in samples]

    return jsonify(data)


@bp.post("/subjects")
def create_subject():
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    name = body.get("name")
    if kind not in VALID_KINDS:
        return jsonify({"error": f"kind must be one of {sorted(VALID_KINDS)}"}), 400
    if not name:
        return jsonify({"error": "name is required"}), 400
    s = Subject(
        kind=kind, name=name,
        description=body.get("description") or "",
        ref_image_paths=body.get("ref_image_paths") or [],
        trigger_word=(body.get("trigger_word") or "").strip() or None,
        voice_id=(body.get("voice_id") or "").strip() or None,
    )
    db.session.add(s); db.session.commit()
    return jsonify(_serialize(s)), 201


@bp.patch("/subjects/<int:subject_id>")
def update_subject(subject_id):
    """Update editable Subject fields. The cast UI uses this to assign a
    character's voice (voice_id) and LoRA trigger word after creation — without
    it, voice_id could only ever be set by the Casting Director's auto-pick."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    if "name" in body and body["name"]:
        s.name = body["name"]
    if "description" in body:
        s.description = body["description"] or ""
    # Empty string clears the field (sets NULL); absent key leaves it untouched.
    if "voice_id" in body:
        s.voice_id = (body["voice_id"] or "").strip() or None
    if "trigger_word" in body:
        s.trigger_word = (body["trigger_word"] or "").strip() or None
    db.session.commit()
    return jsonify(_serialize(s))


@bp.get("/subjects/<int:subject_id>/preview")
def subject_preview(subject_id):
    """Serve a thumbnail for a Subject — its first existing reference image.
    Used by the character picker UI. Falls back to 404 if no image is on disk."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    for p in (s.ref_image_paths or []):
        resolved = _resolve_ref_path(p)
        if resolved:
            return send_file(resolved, max_age=3600)
    return jsonify({"error": "no_preview"}), 404


@bp.get("/subjects/<int:subject_id>/refs/<int:index>/image")
def ref_image(subject_id: int, index: int):
    """Serve the Nth reference image for a Subject (by position in ref_image_paths).
    The preview route only serves the first; this lets the UI render EVERY ref as a
    thumbnail. Resolved through the same containment guard as preview."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    paths = s.ref_image_paths or []
    if index < 0 or index >= len(paths):
        return jsonify({"error": "not_found"}), 404
    resolved = _resolve_ref_path(paths[index])
    if not resolved:
        return jsonify({"error": "no_image"}), 404
    return send_file(resolved, max_age=3600)


@bp.delete("/subjects/<int:subject_id>/refs/<int:index>")
def delete_subject_ref(subject_id: int, index: int):
    """Remove the Nth reference image from a Subject — both the entry in
    ref_image_paths and the file on disk. The Training Data tab calls this so a
    user can prune a bad/over-large reference set (e.g. dropped 57, wants fewer)
    without nuking the whole character. Index is the position in ref_image_paths,
    matching the GET …/refs/<index>/image the thumbnails render from."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    paths = list(s.ref_image_paths or [])
    if index < 0 or index >= len(paths):
        return jsonify({"error": "index_out_of_range"}), 404

    removed = paths.pop(index)
    # Reassign (not in-place mutate) so SQLAlchemy flags the JSON column dirty.
    s.ref_image_paths = paths
    db.session.commit()

    # Best-effort disk cleanup — only delete files that resolve INSIDE an allowed
    # root (same containment guard as serving), so a crafted stored path can't
    # make us unlink something outside cast_refs/.
    try:
        resolved = _resolve_ref_path(removed)
        if resolved and os.path.isfile(resolved):
            os.remove(resolved)
    except OSError as e:
        log.warning("delete_subject_ref: could not unlink %s: %s", removed, e)

    return jsonify(_serialize(s)), 200


@bp.delete("/subjects/<int:subject_id>")
def delete_subject(subject_id):
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    # Best-effort: if this subject had an active training or gen job, mark it
    # failed/cancelled so the unified queue and UI reflect reality. The actual
    # Celery task may still run to completion but will see the subject gone or
    # status changed and short-circuit (idempotency guards exist).
    if s.training_status == "training":
        s.training_status = "failed"
        s.training_error = "Subject deleted — training job cancelled/purged."
    db.session.commit()

    # Future: use unified progress + job ids stored in additional_data or on
    # subject to call cancel_process / job cancel for precise purge.

    db.session.delete(s)
    db.session.commit()
    return "", 204


@bp.post("/subjects/<int:subject_id>/upload-refs")
def upload_subject_refs(subject_id):
    """Drag-and-drop receiver for reference images. Accepts one or more
    multipart files under the ``files`` field, saves them under
    ``data/cast_refs/<subject_id>/``, appends the resolved paths onto
    ``Subject.ref_image_paths``, and returns the updated subject.

    The user-facing flow expects no path-typing — the frontend drops images
    here and the server owns persistence.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "subject not found"}), 404

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files (expected multipart field 'files')"}), 400

    target_dir = _cast_ref_dir(subject_id)
    saved_paths: list[str] = []
    skipped: list[dict] = []

    for f in files:
        if not f or not f.filename:
            continue
        safe_name = secure_filename(f.filename) or ""
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_REF_EXTS:
            skipped.append({"name": f.filename, "reason": f"unsupported extension {ext!r}"})
            continue

        # Resolve collisions by appending -1, -2, … so multiple uploads with
        # the same source filename don't clobber each other.
        stem = Path(safe_name).stem or "ref"
        candidate = target_dir / f"{stem}{ext}"
        n = 1
        while candidate.exists():
            candidate = target_dir / f"{stem}-{n}{ext}"
            n += 1

        # Stream-write with a per-file size cap so a malicious / runaway
        # upload can't fill disk. Anything that goes wrong inside the loop
        # (network drop, disk full, write error) must clean up the partial
        # file — otherwise we leave half-written turds in cast_refs/.
        written = 0
        oversized = False
        write_error = None
        try:
            with open(candidate, "wb") as out:
                while True:
                    chunk = f.stream.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_REF_BYTES:
                        oversized = True
                        break
                    out.write(chunk)
        except OSError as e:
            write_error = e
        if oversized or write_error is not None:
            candidate.unlink(missing_ok=True)
            reason = "too large" if oversized else f"write failed: {write_error}"
            skipped.append({"name": f.filename, "reason": reason})
            continue
        saved_paths.append(str(candidate))

    s.ref_image_paths = list(s.ref_image_paths or []) + saved_paths
    db.session.commit()

    return jsonify({
        "subject": _serialize(s),
        "saved": saved_paths,
        "skipped": skipped,
    })


# ── Character Generator endpoints ─────────────────────────────────────────────
#
# POST  /cast-library/subjects/<id>/plan          — run Casting Director LLM
#                                                   synchronously; persist bible +
#                                                   SubjectSample rows; return them.
# POST  /cast-library/subjects/<id>/generate      — dispatch character.generate_samples
#                                                   (FLUX loop, async Celery task).
# GET   /cast-library/subjects/<id>/samples       — list SubjectSamples.
# POST  /cast-library/subjects/<id>/samples/<sid>/regenerate
#                                                 — dispatch character.regen_sample.
# POST  /cast-library/subjects/<id>/samples/approve
#                                                 — bulk-approve a set of samples.


@bp.post("/subjects/<int:subject_id>/plan")
def plan_character(subject_id: int):
    """Run the Casting Director synchronously to produce bible + shot plan.

    Calls generate_character_sheet() (LLM / Ollama, GPU-free), persists the
    bible and trigger_word on the Subject, deletes any existing SubjectSample
    rows, inserts one row per shot with status=pending, and returns the full
    set.  Images are NOT generated here — dispatch /generate next.

    Request body (JSON, all optional):
      n            int   — number of reference shots to plan (default 32).
      trigger_word str   — force a specific trigger token.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    n = int(body.get("n", 32))
    trigger_word = (body.get("trigger_word") or "").strip() or None

    from backend.services.character_generator_service import generate_character_sheet
    plan = generate_character_sheet(
        name=s.name,
        kind=s.kind,
        description=s.description or "",
        n=n,
        trigger_word=trigger_word or s.trigger_word or None,
    )

    if plan.get("error"):
        return jsonify({"error": plan["error"]}), 502

    bible = plan["bible"]
    trigger = plan["trigger_word"]
    shots = plan["shots"]

    # Persist bible + (new) trigger on the Subject.
    s.bible = bible
    if trigger:
        s.trigger_word = trigger
    db.session.flush()

    # Idempotent: delete existing samples so a re-plan is a clean slate.
    SubjectSample.query.filter_by(subject_id=subject_id).delete()
    db.session.flush()

    rows: list[SubjectSample] = []
    for shot in shots:
        row = SubjectSample(
            subject_id=subject_id,
            index=shot["index"],
            angle=shot.get("angle") or "",
            framing=shot.get("framing") or "",
            expression=shot.get("expression") or "",
            lighting=shot.get("lighting") or "",
            scene=shot.get("scene") or "",
            image_prompt=shot.get("image_prompt") or "",
            placeholder=bool(shot.get("placeholder", False)),
            status="pending",
            approved=False,
        )
        db.session.add(row)
        rows.append(row)

    db.session.commit()

    return jsonify({
        "subject": _serialize(s),
        "bible": bible,
        "trigger_word": trigger,
        "samples": [r.to_dict() for r in rows],
    }), 201


@bp.post("/subjects/<int:subject_id>/generate")
def dispatch_generate_samples(subject_id: int):
    """Dispatch the character.generate_samples Celery task.

    The task re-runs generate_character_sheet, persists a fresh plan, then
    runs the FLUX image loop under gpu_exclusive.  Call /plan first if you
    want to inspect (and possibly edit) the shot plan before committing GPU
    time, or call this endpoint directly to do both in one task.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    progress = get_unified_progress()
    job_id = progress.create_process(
        ProcessType.IMAGE_GENERATION,
        f"Character reference sheet generation for subject {subject_id}",
        additional_data={"subject_id": subject_id, "operation": "generate_samples", "kind": "cast_character_gen"},
    )
    task = celery.send_task("character.generate_samples", args=[subject_id, job_id])
    return jsonify({"task_id": task.id, "job_id": job_id, "subject_id": subject_id}), 202


@bp.get("/subjects/<int:subject_id>/samples")
def list_samples(subject_id: int):
    """Return all SubjectSamples for a Subject, ordered by index."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    samples = (
        SubjectSample.query
        .filter_by(subject_id=subject_id)
        .order_by(SubjectSample.index)
        .all()
    )
    return jsonify({
        "subject_id": subject_id,
        "samples": [r.to_dict() for r in samples],
    })


@bp.get("/subjects/<int:subject_id>/samples/<int:sample_id>/image")
def sample_image(subject_id: int, sample_id: int):
    """Serve a generated SubjectSample's PNG. image_path is an absolute disk path
    (not web-accessible), so resolve it through the same containment guard the
    preview route uses and send_file it. 404 until generation writes the image."""
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404
    resolved = _resolve_ref_path(row.image_path) if row.image_path else None
    if not resolved:
        return jsonify({"error": "no_image"}), 404
    return send_file(resolved, max_age=3600)


@bp.post("/subjects/<int:subject_id>/train")
def dispatch_train(subject_id: int):
    """Dispatch a LoRA training run for this subject directly from the Cast Studio
    (the production casting flow is the only other trigger). The lora_trainer.train_lora
    Celery task is subject-centric (args=[subject_id]) — no Production context needed."""
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404
    if s.training_status == "training":
        return jsonify({"error": "already_training", "subject_id": subject_id}), 409

    # Commit the status transition to 'training' BEFORE dispatching. The
    # lora_trainer.train_lora worker skips any subject not already committed as
    # 'training' (idempotency guard in lora_trainer_tasks.py). Dispatching
    # pre-commit races the worker — it loads the still-'untrained' row and
    # returns immediately, so the button appears to do nothing. Commit first.
    s.training_status = "training"
    s.training_error = None  # clear any prior failure reason before the new run
    db.session.commit()

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    progress = get_unified_progress()
    # Use LORA_TRAIN (or TRAINING) process type so it participates in the unified
    # job queue, Activity, sockets, long-running batch, resume, and cancel paths.
    # additional_data.subject_id allows frontend (and delete purge) to correlate
    # without dedicated kind changes initially. This is the effective architecture
    # choice for the system.
    job_id = progress.create_process(
        ProcessType.TRAINING,  # or IMAGE_GENERATION for consistency with other GPU work; TRAINING fits LoRA intent
        f"LoRA training for cast subject {subject_id} ({s.name})",
        additional_data={"subject_id": subject_id, "operation": "train_lora", "kind": "cast_training"},
    )

    try:
        task = celery.send_task("lora_trainer.train_lora", args=[subject_id, job_id])
    except Exception:
        # Roll the status back so the subject isn't stranded as 'training'
        # forever when the broker is unreachable. Also clean the process.
        try:
            progress.error_process(job_id, "Dispatch failed")
        except Exception:
            pass
        s.training_status = "untrained"
        db.session.commit()
        raise
    return jsonify({"task_id": task.id, "job_id": job_id, "subject_id": subject_id}), 202


@bp.delete("/subjects/<int:subject_id>/samples/<int:sample_id>")
def delete_sample(subject_id: int, sample_id: int):
    """Delete one generated SubjectSample — the UI '✕' on an unwanted generation.
    Leaves the PNG on disk (harmless, in outputs); just drops the row so it
    vanishes from the sheet and never reaches training."""
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404
    db.session.delete(row)
    db.session.commit()
    return "", 204


@bp.post("/subjects/<int:subject_id>/samples/<int:sample_id>/regenerate")
def dispatch_regen_sample(subject_id: int, sample_id: int):
    """Dispatch character.regen_sample for a single SubjectSample.

    Request body (JSON, all optional):
      prompt_override  str  — use this prompt instead of the stored image_prompt.
      seed             int  — fixed seed for reproducibility; omit for random.
    """
    # Validate ownership — the sample must belong to this subject.
    row = db.session.get(SubjectSample, sample_id)
    if row is None or row.subject_id != subject_id:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    prompt_override = (body.get("prompt_override") or "").strip() or None
    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

    from backend.celery_app import celery
    from backend.utils.unified_progress_system import get_unified_progress, ProcessType

    progress = get_unified_progress()
    job_id = progress.create_process(
        ProcessType.IMAGE_GENERATION,
        f"Regen sample {sample_id} for cast subject {subject_id}",
        additional_data={"subject_id": subject_id, "sample_id": sample_id, "operation": "regen_sample", "kind": "cast_character_gen"},
    )
    task = celery.send_task(
        "character.regen_sample",
        args=[sample_id, prompt_override, seed, job_id],
    )
    return jsonify({"task_id": task.id, "job_id": job_id, "sample_id": sample_id}), 202


@bp.post("/subjects/<int:subject_id>/samples/approve")
def approve_samples(subject_id: int):
    """Bulk-approve (or un-approve) a set of SubjectSamples.

    Request body (JSON):
      sample_ids   list[int]   — IDs to act on.
      approved     bool        — True to approve, False to un-approve (default True).

    Only samples that belong to this subject are modified; unknown IDs are
    silently ignored (idempotent — the frontend can fire this on every toggle).
    Returns the updated sample list.
    """
    s = db.session.get(Subject, subject_id)
    if s is None:
        return jsonify({"error": "not_found"}), 404

    body = request.get_json(silent=True) or {}
    sample_ids = body.get("sample_ids")
    if not isinstance(sample_ids, list):
        return jsonify({"error": "sample_ids must be a list of integers"}), 400

    approved_flag = bool(body.get("approved", True))

    rows = (
        SubjectSample.query
        .filter(SubjectSample.subject_id == subject_id)
        .filter(SubjectSample.id.in_(sample_ids))
        .all()
    )
    for row in rows:
        row.approved = approved_flag
    db.session.commit()

    all_samples = (
        SubjectSample.query
        .filter_by(subject_id=subject_id)
        .order_by(SubjectSample.index)
        .all()
    )
    return jsonify({
        "subject_id": subject_id,
        "updated": len(rows),
        "samples": [r.to_dict() for r in all_samples],
    })
