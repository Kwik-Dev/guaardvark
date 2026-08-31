"""The active project, resolved once per run.

Engine modules read company-specific settings through ``current()`` rather than
importing a project directly, so the engine never names a customer.
"""

from __future__ import annotations

from pathlib import Path

from project import Project, load

_active: Project | None = None


def current() -> Project:
    """The active project, loading it from TD_PROJECT on first use."""
    global _active
    if _active is None:
        _active = load()
    return _active


def use(project: Project) -> None:
    """Set the active project explicitly, ahead of any lazy load."""
    global _active
    _active = project


def asset(relative: str | None) -> Path | None:
    """Resolve a project-relative asset path, such as the narrator's clip."""
    if not relative:
        return None
    p = Path(relative).expanduser()
    return p if p.is_absolute() else (current().root / p).resolve()
