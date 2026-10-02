"""Register production-pipeline outputs in the Documents/Folders hierarchy
so they surface in DocumentsPage and ImagesPage automatically.

Folder layout: project_<id>/productions/<prod_id>/<category>/

The same layout is mirrored on disk under UPLOAD_DIR, and ``Document.path`` is
stored relative to UPLOAD_DIR. Renders arrive as absolute paths inside a
temp directory (``tempfile.mkdtemp`` in production_swarm_tasks), which cannot be
served by the document resolver's UPLOAD_BASE join and would vanish when the
OS reclaims the temp directory.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from backend.models import db, Production, Folder, Document
from backend.utils.path_guard import contained

logger = logging.getLogger(__name__)


VALID_CATEGORIES = {"storyboard", "clips", "audio", "final", "timeline"}


def _ensure_folder(name: str, parent_id: int | None, path: str) -> Folder:
    """Look up or create a folder by (parent_id, name). path is the unique
    full virtual path used for the Folder.path column."""
    f = Folder.query.filter_by(parent_id=parent_id, name=name).first()
    if f is not None:
        return f
    f = Folder(name=name, parent_id=parent_id, path=path)
    db.session.add(f)
    db.session.flush()
    return f


def register_production_output(
    *, production: Production, file_path: str, category: str,
) -> Document:
    """Create a Document row for a production output, building the folder
    hierarchy as needed. category must be one of {storyboard, clips, audio, final}.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}"
        )

    # Build the path: project_<n>/productions/<prod_id>/<category>
    if production.project_id is not None:
        root_name = f"project_{production.project_id}"
    else:
        root_name = "orphan"

    root = _ensure_folder(root_name, parent_id=None, path=root_name)
    productions = _ensure_folder(
        "productions", parent_id=root.id, path=f"{root.path}/productions"
    )
    prod_folder = _ensure_folder(
        str(production.id), parent_id=productions.id,
        path=f"{productions.path}/{production.id}",
    )
    leaf = _ensure_folder(
        category, parent_id=prod_folder.id,
        path=f"{prod_folder.path}/{category}",
    )

    # Store a portable, durable path. The bytes are moved under UPLOAD_DIR into
    # the folder we just created, and ``path`` is recorded relative to
    # UPLOAD_DIR so the document resolver's canonical join finds it. An absolute
    # temp path would 404 there, and would break outright once the temp
    # directory is reclaimed.
    from backend.config import UPLOAD_DIR

    upload_base = Path(UPLOAD_DIR)
    dest_dir = contained(upload_base, upload_base / leaf.path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(file_path).name

    p = Path(file_path)
    if p.exists() and p.resolve() != dest.resolve():
        try:
            shutil.move(str(p), str(dest))
        except OSError:
            # A temp dir on another filesystem cannot be renamed across devices.
            shutil.copy2(str(p), str(dest))

    size = dest.stat().st_size if dest.exists() else 0

    doc = Document(
        filename=dest.name,
        path=str(dest.relative_to(upload_base)),
        folder_id=leaf.id,
        size=size,
    )
    db.session.add(doc)
    db.session.commit()
    return doc
