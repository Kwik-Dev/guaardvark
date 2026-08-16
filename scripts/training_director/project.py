"""The customer-project contract for the training-video engine.

The engine is generic: it turns structured procedure guides into narrated video.
Everything that makes the output belong to a particular company — palette,
series wording, trade vocabulary, the narrator's reference clip, and the guides
themselves — lives in that company's own repository and arrives through here.

A project directory contains::

    project.py      defines PROJECT = Project(...)
    guides/         one module per guide, each exposing SCRIPT

and is selected with the ``TD_PROJECT`` environment variable or ``--project``.
With neither, the engine runs unbranded, which is only useful for smoke tests.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

Color = tuple[int, int, int]


@dataclass(frozen=True)
class Project:
    """Everything the engine needs to speak in one company's voice."""

    name: str
    series_label: str

    # Written -> spoken overrides for this trade. The engine owns the numeric
    # and code-citation machinery; the vocabulary is the customer's.
    terms: dict[str, str] = field(default_factory=dict)
    # Acronyms this trade expects spelled out letter by letter.
    spelled_acronyms: tuple[str, ...] = ()

    # Brand palette used by every composited card.
    ink: Color = (14, 27, 48)
    paper: Color = (247, 249, 252)
    accent: Color = (198, 156, 74)
    rule: Color = (176, 190, 208)

    # Narrator. 'kokoro' speaks a catalogue voice named by voice_id and needs
    # no recording; 'chatterbox' clones voice_reference, and voice_emotion
    # selects its delivery preset (Chatterbox only — Kokoro ignores it).
    voice_backend: str = "kokoro"
    voice_id: str = "am_onyx"
    voice_reference: str | None = None
    voice_emotion: str = "narration"

    # Resolved by the loader; not set by hand.
    root: Path = Path(".")


UNBRANDED = Project(name="Training", series_label="Training")


def load(project_dir: str | os.PathLike | None = None) -> Project:
    """Load ``project.py`` from a project directory and return its PROJECT.

    The project directory is deliberately NOT added to ``sys.path``: it holds a
    ``project.py`` of its own, which would shadow this module. Guides are loaded
    by file path instead — see ``produce.load_guide``.
    """
    raw = project_dir or os.environ.get("TD_PROJECT")
    if not raw:
        return UNBRANDED

    root = Path(raw).expanduser().resolve()
    module_path = root / "project.py"
    if not module_path.is_file():
        raise SystemExit(f"no project.py in {root}")

    spec = importlib.util.spec_from_file_location("td_project", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project = getattr(module, "PROJECT", None)
    if not isinstance(project, Project):
        raise SystemExit(f"{module_path} must define PROJECT = Project(...)")

    object.__setattr__(project, "root", root)
    return project


def load_module(path: Path, name: str):
    """Import a single file as a module without touching ``sys.path``."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
