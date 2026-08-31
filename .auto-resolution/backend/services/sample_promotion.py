"""Promote approved generated samples into durable Training Data after train.

After a successful *real* LoRA train, approved SubjectSamples that were fed into
the trainer graduate off the Generate Character sheet:

  1. ``promoted_to_training=True`` (+ ``promoted_at``) so the UI hides them from
     Generate Character while still listing them under Training Data history.
  2. Their ``image_path`` is appended to ``Subject.ref_image_paths`` (deduped) so
     the Training Data tab thumbnails and every future train/amend keep them
     even if the sample row is later re-planned away.

Until promotion, approved samples remain visible on *both* tabs so the user can
see the pending pool regardless of which tab they are on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from backend.models import db, Subject, SubjectSample

logger = logging.getLogger(__name__)


def promote_samples_after_train(
    subject: Subject,
    used_images: Iterable[str] | None = None,
) -> dict:
    """Mark used approved samples as promoted and fold their paths into refs.

    Idempotent: already-promoted samples are left alone; paths already in
    ``ref_image_paths`` are not duplicated.

    Returns ``{promoted: int, paths_added: list[str], ref_image_paths: list}``.
    """
    used = [p for p in (used_images or []) if p]
    used_set = set(used)

    refs = list(subject.ref_image_paths or [])
    ref_set = set(refs)
    paths_added: list[str] = []
    promoted = 0

    # Prefer samples whose path was actually used in this train run. If the
    # caller passed an empty used list (should not happen on real success), fall
    # back to every approved/done sample so we still graduate the curated set.
    candidates = (
        SubjectSample.query
        .filter_by(subject_id=subject.id, approved=True, status="done")
        .filter(SubjectSample.promoted_to_training.is_(False))
        .all()
    )
    if used_set:
        candidates = [s for s in candidates if s.image_path and s.image_path in used_set]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for smp in candidates:
        path = smp.image_path
        if not path:
            continue
        smp.promoted_to_training = True
        smp.promoted_at = now
        promoted += 1
        if path not in ref_set:
            refs.append(path)
            ref_set.add(path)
            paths_added.append(path)

    if promoted or paths_added:
        subject.ref_image_paths = refs
        db.session.add(subject)
        logger.info(
            "sample_promotion: subject %s promoted %d sample(s), added %d ref path(s)",
            subject.id, promoted, len(paths_added),
        )

    return {
        "promoted": promoted,
        "paths_added": paths_added,
        "ref_image_paths": refs,
    }
