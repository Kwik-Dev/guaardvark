"""Refuse a generation load when the Hugging Face snapshot is not local.

The Audio Studio Manage-models modal is the only path that fetches these
repos. `from_pretrained` / `hf_hub_download` would otherwise pull gigabytes
on the first Generate.
"""

from __future__ import annotations

INSTALL_HINT = (
    "Open Audio Studio → Manage models and Install this model. "
    "Generation never downloads on its own."
)


class WeightsNotInstalled(RuntimeError):
    """The weights are not on this machine and this code path will not fetch them."""


def require_hub_files(repo_id: str, files: list[str], purpose: str) -> None:
    """Raise WeightsNotInstalled if any `files` are missing from the HF cache."""
    try:
        from huggingface_hub import try_to_load_from_cache
        from huggingface_hub.file_download import _CACHED_NO_EXIST
    except Exception as e:  # pragma: no cover
        raise WeightsNotInstalled(
            f"{purpose}: cannot probe the Hugging Face cache ({e}). {INSTALL_HINT}"
        ) from e

    missing: list[str] = []
    for name in files:
        try:
            hit = try_to_load_from_cache(repo_id, name)
        except Exception:
            hit = None
        if not isinstance(hit, str) or hit is _CACHED_NO_EXIST:
            missing.append(name)
    if missing:
        raise WeightsNotInstalled(
            f"{purpose}: weights for '{repo_id}' are not on this machine "
            f"(missing {missing[0]}). {INSTALL_HINT}"
        )
