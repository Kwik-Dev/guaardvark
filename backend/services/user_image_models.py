"""User-added image models — Hugging Face repos and files beside the shipped catalog.

The shipped stills catalog lives on OfflineImageGenerator.available_models. A
person can add three kinds of thing from a pasted Hugging Face URL:

  generation / snapshot     a diffusers repo (has model_index.json), cloned as a
                            new model of a shipped family: Z-Image, Krea 2, SDXL, SD
  generation / single_file  one .safetensors checkpoint, loaded with
                            from_single_file; SD and SDXL only, the families
                            diffusers can rebuild from a merged checkpoint
  lora                      an adapter file the offline pipeline stacks on a model
                            of its family (Z-Image today; SDXL and FLUX LoRAs run
                            on the ComfyUI path, which this catalog does not drive)

Entries persist in data/user_image_models.json (gitignored) and register into the
generator at construction so the picker, the downloader and the loader see them.
Ids are ``user-<family>-<slug>``: the family sits in the id because the sampling
defaults (stills_defaults.model_family) and the page (batchImageSettings.modelFamily)
read the family off the id string, and the slug is scrubbed of the other families'
markers so that read cannot be fooled.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

USER_MODEL_PREFIX = "user-"
DEFAULT_LORA_STRENGTH = 0.8
_CATALOG_LOCK = threading.Lock()
_CATALOG_PATH_OVERRIDE = None
WEIGHT_SUFFIXES = (".safetensors", ".ckpt", ".pt", ".bin")

# Families a user model can join, the shipped key whose sampling policy it
# borrows, and whether diffusers can load it from a single merged checkpoint.
GENERATION_FAMILIES = {
    "zimage": {"label": "Z-Image", "like": "zimage-turbo", "single_file": False},
    "krea2": {"label": "Krea 2", "like": "krea2-turbo", "single_file": False},
    "sdxl": {"label": "SDXL", "like": "sd-xl", "single_file": True},
    "sd": {"label": "SD 1.5", "like": "realistic-vision", "single_file": True},
}
# OfflineImageGenerator._apply_loras implements Z-Image only; SDXL and FLUX
# character LoRAs are routed to ComfyUI, which the offline catalog cannot drive.
LORA_FAMILIES = ("zimage",)
# Markers each family's id detection keys on; scrubbed from a slug that belongs
# to a different family.
_FAMILY_MARKERS = {
    "zimage": ("zimage", "z-image"),
    "krea2": ("krea",),
    "sdxl": ("xl",),
    "flux": ("flux",),
}
ROLES = ("generation", "lora")


def user_catalog_path() -> Path:
    if _CATALOG_PATH_OVERRIDE is not None:
        return Path(_CATALOG_PATH_OVERRIDE)
    root = os.environ.get("GUAARDVARK_ROOT", ".")
    return Path(root) / "data" / "user_image_models.json"


def is_user_model_id(model_id: str) -> bool:
    return str(model_id or "").startswith(USER_MODEL_PREFIX)


def family_safe_slug(text: str, family: str) -> str:
    """A slug that no other family's id detection will claim."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    for fam, markers in _FAMILY_MARKERS.items():
        if fam == family:
            continue
        for marker in markers:
            s = s.replace(marker, "-")
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40] or "model"


def user_model_id(name: str, family: str, taken=None) -> str:
    base = f"{USER_MODEL_PREFIX}{family}-{family_safe_slug(name, family)}"
    taken = set(taken or ())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def suggest_role_and_family(files: list, src: str | None, hf_repo: str, has_model_index: bool):
    """Guess LoRA vs generation and the family from names alone."""
    names = [src] if src else [f.get("src") or "" for f in files]
    blob = " ".join(names + [hf_repo or ""]).lower()
    if any(k in blob for k in ("lora", "lycoris", "adapter")):
        role = "lora"
    else:
        role = "generation"
    if "z-image" in blob or "zimage" in blob:
        family = "zimage"
    elif "krea" in blob:
        family = "krea2"
    elif "flux" in blob:
        family = "flux"
    elif "xl" in blob:
        family = "sdxl"
    elif has_model_index or role == "generation":
        family = "sd"
    else:
        family = "zimage"
    return role, family


def preview_hf_url(url: str) -> dict:
    """Parse a paste and list the repo's weight files; says whether it is a diffusers repo."""
    from backend.services.user_video_models import parse_hf_url, list_hf_weight_files
    from huggingface_hub import HfApi

    parsed = parse_hf_url(url)
    repo, revision = parsed["hf_repo"], parsed.get("revision") or "main"
    files, gated = list_hf_weight_files(repo, revision)
    try:
        all_files = HfApi().list_repo_files(repo, revision=revision)
    except Exception:  # noqa: BLE001 — the weight listing already succeeded
        all_files = []
    has_model_index = "model_index.json" in all_files
    src = parsed.get("src")
    if src and not any(f["src"] == src for f in files):
        files.insert(0, {"src": src, "size": 0})
    role, family = suggest_role_and_family(files, src, repo, has_model_index)
    return {
        "hf_repo": repo,
        "revision": revision,
        "src": src,
        "files": files,
        "gated": gated,
        "has_model_index": has_model_index,
        "suggested_role": role,
        "suggested_family": family,
        "families": family_choices(),
    }


def family_choices() -> list:
    return [
        {"id": fam, "label": spec["label"], "like": spec["like"],
         "single_file": spec["single_file"], "lora": fam in LORA_FAMILIES}
        for fam, spec in GENERATION_FAMILIES.items()
    ]


def _read_catalog() -> dict:
    path = user_catalog_path()
    if not path.exists():
        return {"models": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("user image catalog unreadable (%s): %s", path, e)
        return {"models": {}}
    models = data.get("models") if isinstance(data, dict) else None
    return {"models": models if isinstance(models, dict) else {}}


def _write_catalog(catalog: dict) -> None:
    path = user_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def build_user_entry(
    *,
    role: str,
    family: str,
    hf_repo: str,
    files: list,
    has_model_index: bool = False,
    name: str | None = None,
    description: str | None = None,
    revision: str = "main",
    taken=None,
) -> tuple[str, dict]:
    """Validate the pieces and shape a catalog entry. Does not persist or register."""
    if role not in ROLES:
        raise ValueError("Role must be 'generation' (a model to pick) or 'lora' (an adapter on a model).")
    if family not in GENERATION_FAMILIES:
        raise ValueError(
            f"Family must be one of {', '.join(GENERATION_FAMILIES)}. FLUX models install "
            "from Manage Video Models, which owns the ComfyUI folders."
        )
    if not hf_repo or "/" not in hf_repo:
        raise ValueError("hf_repo must be org/repo.")
    specs = []
    total = 0
    for item in files or []:
        src = (item.get("src") if isinstance(item, dict) else None) or ""
        if src:
            specs.append({"src": src, "dst": Path(src).name})
            total += int((item.get("size") if isinstance(item, dict) else 0) or 0)
    size_gb = round(total / (1024 ** 3), 3) if total else 0.0
    spec = GENERATION_FAMILIES[family]

    if role == "lora":
        if family not in LORA_FAMILIES:
            raise ValueError(
                f"{spec['label']} LoRAs are not stacked by the offline image engine yet; "
                f"that works for {', '.join(GENERATION_FAMILIES[f]['label'] for f in LORA_FAMILIES)} today."
            )
        if len(specs) != 1:
            raise ValueError("A LoRA is one file. Pick the single .safetensors to stack.")
        kind = "lora"
    elif has_model_index:
        if specs and len(specs) > 0:
            # A diffusers repo is fetched whole; a file list would only confuse the
            # snapshot downloader, which already skips redundant root checkpoints.
            specs = []
        kind = "snapshot"
    else:
        if not spec["single_file"]:
            raise ValueError(
                f"{spec['label']} models load from a diffusers repo (one with model_index.json); "
                "this repo has none. Paste the repo root of a diffusers build."
            )
        if len(specs) != 1:
            raise ValueError("A single-file checkpoint is one .safetensors. Pick exactly one.")
        kind = "single_file"

    stem = name or (Path(specs[0]["src"]).stem if specs else hf_repo.split("/")[-1])
    mid = user_model_id(stem, family, taken)
    entry = {
        "name": name or stem,
        "description": description or (
            f"User {'LoRA' if role == 'lora' else 'model'} · {spec['label']} · {hf_repo}"
        ),
        "role": role,
        "family": family,
        "kind": kind,
        "like": spec["like"],
        "hf_repo": hf_repo,
        "revision": revision or "main",
        "files": specs,
        "size_gb": size_gb,
        "applies_to": [family] if role == "lora" else [],
        "strength": DEFAULT_LORA_STRENGTH if role == "lora" else None,
        "user": True,
    }
    return mid, entry


# ── registration into the offline generator ─────────────────────────────────

def user_sentinel(model_id: str) -> str:
    return f"user:{model_id}"


def register_entry(generator, model_id: str, entry: dict) -> None:
    """Teach an OfflineImageGenerator about one entry (idempotent)."""
    for attr in ("family_overrides", "user_files", "user_entries"):
        if not isinstance(getattr(generator, attr, None), dict):
            setattr(generator, attr, {})
    if not isinstance(getattr(generator, "hidden_models", None), set):
        generator.hidden_models = set(getattr(generator, "hidden_models", ()) or ())
    family = entry.get("family")
    generator.user_entries[model_id] = dict(entry)
    generator.family_overrides[model_id] = family
    if entry.get("kind") == "snapshot":
        generator.available_models[model_id] = entry["hf_repo"]
        generator.family_overrides[entry["hf_repo"]] = family
    else:
        sentinel = user_sentinel(model_id)
        generator.available_models[model_id] = sentinel
        generator.family_overrides[sentinel] = family
        generator.user_files[sentinel] = {
            "dir": model_id,
            "hf_repo": entry["hf_repo"],
            "revision": entry.get("revision") or "main",
            "files": list(entry.get("files") or []),
            "kind": entry.get("kind"),
            "family": family,
        }
    if entry.get("role") == "lora":
        generator.hidden_models.add(model_id)
    generator.model_meta[model_id] = {
        "label": entry.get("name") or model_id,
        "description": entry.get("description") or "",
        "recommended": False,
        "order": 50 + len(generator.user_entries),
        "engine": "offline",
        "user": True,
        "role": entry.get("role"),
        "family": family,
        "kind": entry.get("kind"),
        "size_gb": entry.get("size_gb") or 0.0,
        "applies_to": list(entry.get("applies_to") or []),
        "strength": entry.get("strength"),
    }


def unregister_entry(generator, model_id: str) -> None:
    sentinel = user_sentinel(model_id)
    for attr in ("available_models", "model_meta", "user_entries", "family_overrides"):
        d = getattr(generator, attr, None)
        if isinstance(d, dict):
            d.pop(model_id, None)
    if isinstance(getattr(generator, "family_overrides", None), dict):
        generator.family_overrides.pop(sentinel, None)
    if isinstance(getattr(generator, "user_files", None), dict):
        generator.user_files.pop(sentinel, None)
    if isinstance(getattr(generator, "hidden_models", None), set):
        generator.hidden_models.discard(model_id)


def load_user_catalog(generator) -> int:
    """Register every persisted entry. Safe at construction; a bad entry is skipped."""
    catalog = _read_catalog()
    n = 0
    for mid, entry in (catalog.get("models") or {}).items():
        if not is_user_model_id(mid) or not isinstance(entry, dict):
            logger.error("skipping user image catalog id %r — not a user-* entry", mid)
            continue
        try:
            register_entry(generator, mid, entry)
            n += 1
        except Exception as e:  # noqa: BLE001 — one bad row must not hide the rest
            logger.error("user image model %s failed to register: %s", mid, e)
    return n


def add_user_model(generator, **kwargs) -> tuple[str, dict]:
    """Validate, persist and register. Returns (id, entry)."""
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        taken = set(catalog["models"]) | set(getattr(generator, "available_models", {}) or {})
        mid, entry = build_user_entry(taken=taken, **kwargs)
        register_entry(generator, mid, entry)
        catalog["models"][mid] = entry
        _write_catalog(catalog)
    return mid, entry


def remove_user_model(generator, model_id: str, *, delete_files: bool = False) -> dict:
    """Drop a user entry. Shipped keys raise. Optionally delete its files."""
    if not is_user_model_id(model_id):
        raise ValueError("Only user-added models can be removed. The shipped catalog stays.")
    with _CATALOG_LOCK:
        catalog = _read_catalog()
        entry = catalog["models"].pop(model_id, None) or dict(
            (getattr(generator, "user_entries", {}) or {}).get(model_id) or {}
        )
        if not entry:
            raise KeyError(f"Unknown user model '{model_id}'")
        unregister_entry(generator, model_id)
        _write_catalog(catalog)
    removed = []
    if delete_files:
        base = Path(generator.models_dir)
        target = base / (entry["hf_repo"].replace("/", "--") if entry.get("kind") == "snapshot" else model_id)
        # A snapshot directory can be shared with a shipped model of the same repo.
        shared = entry.get("kind") == "snapshot" and any(
            v == entry.get("hf_repo") for k, v in (generator.available_models or {}).items() if k != model_id
        )
        if target.is_dir() and not shared:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            removed.append(str(target))
    return {"id": model_id, "name": entry.get("name"), "deleted_paths": removed}


# ── generation-time helpers ─────────────────────────────────────────────────

def user_files_present(generator, sentinel: str) -> bool:
    uf = (getattr(generator, "user_files", {}) or {}).get(sentinel)
    if not uf:
        return False
    base = Path(generator.models_dir) / uf["dir"]
    for spec in uf.get("files") or []:
        p = base / spec["dst"]
        if not p.is_file() or p.stat().st_size == 0:
            return False
    return bool(uf.get("files"))


def download_user_files(generator, sentinel: str) -> tuple[bool, str | None]:
    """hf_hub_download each declared file into models_dir/<id>/, resuming partials."""
    from huggingface_hub import hf_hub_download

    uf = (getattr(generator, "user_files", {}) or {}).get(sentinel)
    if not uf:
        return False, f"{sentinel} is not a user file entry"
    base = Path(generator.models_dir) / uf["dir"]
    base.mkdir(parents=True, exist_ok=True)
    try:
        for spec in uf.get("files") or []:
            got = Path(hf_hub_download(
                repo_id=uf["hf_repo"], filename=spec["src"], revision=uf.get("revision") or "main",
                local_dir=str(base),
            ))
            want = base / spec["dst"]
            if got.resolve() != want.resolve():
                want.parent.mkdir(parents=True, exist_ok=True)
                got.replace(want)
        if uf.get("kind") == "lora":
            _write_lora_sidecar(base, uf)
        return True, None
    except Exception as e:  # noqa: BLE001 — the message is the diagnosis
        logger.error("user image file download failed for %s: %s", sentinel, e)
        return False, str(e)


def _write_lora_sidecar(base: Path, uf: dict) -> None:
    """Sidecar the character pipeline reads for family routing, minus the subject."""
    from backend.services.media_model_registry import get_profile

    spec = GENERATION_FAMILIES.get(uf.get("family") or "", {})
    profile = get_profile(spec.get("like")) or {}
    for f in uf.get("files") or []:
        side = (base / f["dst"]).with_suffix(".json")
        if side.exists():
            continue
        side.write_text(json.dumps({
            "base_model_id": profile.get("id") or spec.get("like"),
            "lora_format": profile.get("lora_format"),
            "family": uf.get("family"),
            "user_lora": True,
            "schema_version": 2,
        }, indent=2), encoding="utf-8")


def resolve_user_loras(generator, model: str, adapters: list) -> tuple[list, float | None, str | None]:
    """User LoRA ids → (file paths, strength, None) or ([], None, message)."""
    if not adapters:
        return [], None, None
    entries = getattr(generator, "user_entries", {}) or {}
    try:
        from backend.services.stills_defaults import model_family
        fam = model_family(model)
        fam = {"krea2-turbo": "krea2", "krea2-raw": "krea2"}.get(fam, fam)
    except Exception:  # noqa: BLE001
        fam = None
    paths = []
    strength = None
    for raw in adapters:
        aid = raw.get("id") if isinstance(raw, dict) else raw
        aid = (aid or "").strip()
        if not aid:
            continue
        entry = entries.get(aid) or {}
        label = entry.get("name") or aid
        if entry.get("role") != "lora":
            return [], None, f"'{aid}' is not a LoRA you added."
        applies = entry.get("applies_to") or []
        if fam and applies and fam not in applies:
            return [], None, f"{label} is a {GENERATION_FAMILIES.get(applies[0], {}).get('label', applies[0])} LoRA and does not apply to this model."
        sentinel = user_sentinel(aid)
        if not user_files_present(generator, sentinel):
            return [], None, f"{label} is not installed. Open Manage Image Models to download it."
        uf = generator.user_files[sentinel]
        paths.append(str(Path(generator.models_dir) / uf["dir"] / uf["files"][0]["dst"]))
        s = raw.get("strength") if isinstance(raw, dict) else None
        s = float(s if s is not None else entry.get("strength") or DEFAULT_LORA_STRENGTH)
        strength = s if strength is None else max(strength, s)
    return paths, strength, None


def catalog_rows(generator) -> list:
    """Rows for the LoRA side of the models list (the picker never shows these)."""
    rows = []
    for mid, entry in (getattr(generator, "user_entries", {}) or {}).items():
        if entry.get("role") != "lora":
            continue
        rows.append({
            "id": mid,
            "name": entry.get("name") or mid,
            "description": entry.get("description") or "",
            "type": "lora",
            "family": entry.get("family"),
            "applies_to": list(entry.get("applies_to") or []),
            "hf_repo": entry.get("hf_repo"),
            "size_gb": entry.get("size_gb") or 0.0,
            "strength": entry.get("strength") or DEFAULT_LORA_STRENGTH,
            "is_downloaded": user_files_present(generator, user_sentinel(mid)),
            "user": True,
        })
    return rows
