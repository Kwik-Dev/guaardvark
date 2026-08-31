"""Media model registry — single source of truth for stills + character LoRA bases.

Mirrors the philosophy of the Ollama model selector and video_model_registry:
profiles are declared here; Settings pick defaults; Cast/LoRA train + inference
must agree on `base_model_id` (no more silent "force SDXL when any LoRA").

Roles:
  - stills_t2i: default / chat / batch image generation
  - lora_train: character/environment/prop LoRA training target
  - max_quality: optional higher-ceiling stills (FLUX-dev)

Train backends are pluggable. Only backends with train_ready=True may run.
Z-Image is the product default; FLUX is max-quality; SDXL is legacy only.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Profile IDs (stable API / DB values) ─────────────────────────────────────
ZIMAGE_TURBO = "zimage-turbo"
FLUX_DEV = "flux-dev"
SDXL_LEGACY = "sdxl-legacy"
KREA2_TURBO = "krea2-turbo"
AUTO = "auto"

DEFAULT_STILLS_MODEL = ZIMAGE_TURBO
DEFAULT_CAST_TRAIN_BASE = ZIMAGE_TURBO
DEFAULT_MAX_QUALITY_MODEL = FLUX_DEV

# Setting keys (settings table via get_setting / save_setting)
SETTING_STILLS_MODEL = "media_stills_model"
SETTING_CAST_TRAIN_BASE = "media_cast_train_base"
SETTING_MAX_QUALITY_MODEL = "media_max_quality_model"

ENV_STILLS = "GUAARDVARK_STILLS_MODEL"
ENV_CAST_TRAIN = "GUAARDVARK_CAST_TRAIN_BASE"
ENV_MAX_QUALITY = "GUAARDVARK_MAX_QUALITY_MODEL"


MEDIA_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    ZIMAGE_TURBO: {
        "id": ZIMAGE_TURBO,
        "name": "Z-Image Turbo",
        "description": (
            "Default stills + character LoRA base. Fast, strong prompt adherence, "
            "best daily-driver quality on 16GB GPUs."
        ),
        "family": "zimage",
        "roles": ["stills_t2i", "lora_train"],
        "recommended": True,
        "tier": "default",
        # Diffusers / offline_image_generator catalog key
        "offline_model_key": "zimage-turbo",
        "hf_id": "Tongyi-MAI/Z-Image-Turbo",
        "lora_format": "zimage",
        "inference_engine": "offline",  # offline_image_generator (+ future Comfy)
        "comfy_model_tag": None,
        "train_backend": "peft_zimage",
        "train_ready": True,
        "train_status_note": (
            "PEFT flow-matching LoRA on Z-Image Turbo (16GB-safe: cache latents, "
            "768 default). Optional Ostris turbo adapter via ZIMAGE_TURBO_TRAIN_ADAPTER."
        ),
        "vram_train_mb": 12000,
        "vram_infer_mb": 11000,
        "order": 0,
    },
    FLUX_DEV: {
        "id": FLUX_DEV,
        "name": "FLUX.1 Dev",
        "description": (
            "Max-quality stills / character path. Heavier, slower, stronger ceiling "
            "and mature LoRA ecosystem."
        ),
        "family": "flux",
        "roles": ["stills_t2i", "lora_train", "max_quality"],
        "recommended": False,
        "tier": "max_quality",
        "offline_model_key": None,  # Comfy path today
        "hf_id": "black-forest-labs/FLUX.1-dev",
        "lora_format": "flux",
        "inference_engine": "comfy",
        "comfy_model_tag": "flux-dev",
        "train_backend": "ai_toolkit_flux",
        "train_ready": False,
        "train_status_note": (
            "FLUX character training will use a dedicated recipe (quantized on 16GB). "
            "Not the product default — use for max-quality identity."
        ),
        "vram_train_mb": 14000,
        "vram_infer_mb": 12000,
        "order": 1,
    },
    KREA2_TURBO: {
        "id": KREA2_TURBO,
        "name": "Krea 2 Turbo",
        "description": "Optional stills model. High quality but much slower than Z-Image on this box.",
        "family": "krea2",
        "roles": ["stills_t2i"],
        "recommended": False,
        "tier": "optional",
        "offline_model_key": "krea2-turbo",
        "hf_id": "krea/Krea-2-Turbo",
        "lora_format": None,  # not a cast train base
        "inference_engine": "offline",
        "comfy_model_tag": None,
        "train_backend": None,
        "train_ready": False,
        "train_status_note": "Not used for character LoRA training.",
        "vram_train_mb": 0,
        "vram_infer_mb": 14000,
        "order": 2,
    },
    SDXL_LEGACY: {
        "id": SDXL_LEGACY,
        "name": "SDXL (Legacy)",
        "description": (
            "Deprecated identity base. Kept only so existing SDXL LoRAs and the "
            "current PEFT trainer still work until Z-Image/FLUX trainers ship."
        ),
        "family": "sdxl",
        "roles": ["stills_t2i", "lora_train"],
        "recommended": False,
        "tier": "legacy",
        "offline_model_key": "sd-xl",
        "hf_id": "stabilityai/stable-diffusion-xl-base-1.0",
        "lora_format": "kohya_sdxl",
        "inference_engine": "comfy",
        "comfy_model_tag": "sdxl",
        "train_backend": "peft_sdxl",
        "train_ready": True,  # only fully wired trainer today
        "train_status_note": "Legacy. Prefer Z-Image once its train backend is ready.",
        "vram_train_mb": 12000,
        "vram_infer_mb": 8000,
        "order": 99,
        "deprecated": True,
    },
}


def get_profile(model_id: str | None) -> Optional[dict[str, Any]]:
    if not model_id:
        return None
    mid = str(model_id).strip().lower()
    if mid in ("sd-xl", "sdxl", "sdxl-base", "sdxl-base-1.0"):
        mid = SDXL_LEGACY
    if mid in ("flux", "flux.1-dev", "flux1-dev"):
        mid = FLUX_DEV
    if mid in ("z-image-turbo", "zimage", "tongyi-mai/z-image-turbo"):
        mid = ZIMAGE_TURBO
    return MEDIA_MODEL_REGISTRY.get(mid)


def list_profiles(
    *,
    role: str | None = None,
    include_legacy: bool = True,
) -> list[dict[str, Any]]:
    rows = []
    for p in MEDIA_MODEL_REGISTRY.values():
        if role and role not in (p.get("roles") or []):
            continue
        if not include_legacy and p.get("deprecated"):
            continue
        rows.append(dict(p))
    rows.sort(key=lambda r: (r.get("order", 50), r.get("name") or ""))
    return rows


def resolve_stills_model(requested: str | None = None) -> str:
    """Resolve stills model id for generation (auto → Settings default)."""
    req = (requested or "").strip().lower()
    if req and req != AUTO:
        # Accept offline catalog keys that map into registry
        if req in MEDIA_MODEL_REGISTRY:
            return req
        p = get_profile(req)
        if p:
            return p["id"]
        # Pass through offline keys (realistic-vision, etc.) outside registry
        return req
    return get_stills_model_setting()


def get_stills_model_setting() -> str:
    return _read_setting(SETTING_STILLS_MODEL, ENV_STILLS, DEFAULT_STILLS_MODEL)


def get_cast_train_base_setting() -> str:
    return _read_setting(SETTING_CAST_TRAIN_BASE, ENV_CAST_TRAIN, DEFAULT_CAST_TRAIN_BASE)


def get_max_quality_model_setting() -> str:
    return _read_setting(SETTING_MAX_QUALITY_MODEL, ENV_MAX_QUALITY, DEFAULT_MAX_QUALITY_MODEL)


def set_stills_model_setting(model_id: str) -> str:
    return _write_setting(SETTING_STILLS_MODEL, model_id, allow_auto=True)


def set_cast_train_base_setting(model_id: str) -> str:
    mid = _normalize_train_base(model_id)
    return _write_setting(SETTING_CAST_TRAIN_BASE, mid, allow_auto=False)


def set_max_quality_model_setting(model_id: str) -> str:
    mid = (model_id or DEFAULT_MAX_QUALITY_MODEL).strip().lower()
    if mid not in MEDIA_MODEL_REGISTRY:
        raise ValueError(f"Unknown max-quality model: {model_id}")
    return _write_setting(SETTING_MAX_QUALITY_MODEL, mid, allow_auto=False)


def _normalize_train_base(model_id: str) -> str:
    p = get_profile(model_id)
    if not p:
        raise ValueError(f"Unknown cast train base: {model_id}")
    if "lora_train" not in (p.get("roles") or []):
        raise ValueError(f"{p['id']} is not a character LoRA train base")
    return p["id"]


def _read_setting(key: str, env_name: str, default: str) -> str:
    try:
        from backend.utils.settings_utils import get_setting
        val = get_setting(key, default=None)
        if val:
            return str(val).strip().lower()
    except Exception:
        pass
    env = os.environ.get(env_name, "").strip().lower()
    if env:
        return env
    return default


def _write_setting(key: str, model_id: str, *, allow_auto: bool) -> str:
    mid = (model_id or "").strip().lower() or (AUTO if allow_auto else "")
    if allow_auto and mid == AUTO:
        pass
    elif mid not in MEDIA_MODEL_REGISTRY and not (allow_auto and mid == AUTO):
        # stills may use offline keys not in registry (e.g. realistic-vision)
        if not allow_auto:
            raise ValueError(f"Unknown model id: {model_id}")
    try:
        from backend.utils.settings_utils import save_setting
        save_setting(key, mid)
    except Exception as e:
        logger.warning("media_model_registry: save_setting(%s) failed: %s", key, e)
    return mid


def subject_base_model_id(subject) -> str:
    """Base model this subject's LoRA is trained for (or will be)."""
    raw = getattr(subject, "training_settings_json", None) or {}
    if isinstance(raw, dict):
        bid = raw.get("base_model_id") or raw.get("base_model")
        if bid:
            p = get_profile(str(bid))
            if p:
                return p["id"]
    # Infer from existing LoRA sidecar if present
    lp = getattr(subject, "lora_path", None)
    if lp:
        meta = read_lora_sidecar(lp)
        if meta and meta.get("base_model_id"):
            p = get_profile(str(meta["base_model_id"]))
            if p:
                return p["id"]
        # Historic LoRAs with no sidecar field → SDXL (old trainer)
        if meta is not None or (lp and Path(lp).is_file()):
            return SDXL_LEGACY
    return get_cast_train_base_setting()


def assert_train_ready(base_model_id: str) -> dict[str, Any]:
    """Return profile or raise ValueError if training cannot run."""
    p = get_profile(base_model_id)
    if not p:
        raise ValueError(f"Unknown train base: {base_model_id}")
    if "lora_train" not in (p.get("roles") or []):
        raise ValueError(f"{p['id']} does not support LoRA training")
    if not p.get("train_ready"):
        note = p.get("train_status_note") or "Trainer not ready for this base."
        raise ValueError(
            f"Character training for '{p['name']}' is not ready yet. {note} "
            f"Use Settings → Media models → Cast train base → '{ZIMAGE_TURBO}' "
            f"(default) or '{SDXL_LEGACY}' (legacy)."
        )
    return p


def lora_compatible_with_inference(base_model_id: str, inference_model: str | None) -> bool:
    """True if a LoRA trained for base_model_id can be applied under inference_model."""
    base = get_profile(base_model_id)
    if not base:
        return False
    inf = (inference_model or "").strip().lower()
    if not inf or inf == AUTO:
        # Default stills path must match train base family for cast
        stills = get_stills_model_setting()
        if stills == AUTO:
            stills = DEFAULT_STILLS_MODEL
        inf_profile = get_profile(stills)
    else:
        inf_profile = get_profile(inf)
        if not inf_profile and inf in ("sd-xl", "sdxl"):
            inf_profile = get_profile(SDXL_LEGACY)
        if not inf_profile and "flux" in inf:
            inf_profile = get_profile(FLUX_DEV)
        if not inf_profile and ("zimage" in inf or "z-image" in inf):
            inf_profile = get_profile(ZIMAGE_TURBO)
    if not inf_profile:
        # Unknown offline key: only OK if same string as offline_model_key
        return base.get("offline_model_key") == inf
    return base.get("family") == inf_profile.get("family")


def read_lora_sidecar(lora_path: str | None) -> Optional[dict[str, Any]]:
    if not lora_path:
        return None
    p = Path(lora_path)
    side = p.with_suffix(".json")
    if not side.is_file():
        return None
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_lora_sidecar(
    lora_path: str | Path,
    *,
    subject_id: int,
    subject_name: str,
    trigger_word: str,
    base_model_id: str,
    ref_count: int,
    steps: int | None = None,
    mock: bool = False,
    extra: dict | None = None,
) -> Path:
    """Write the mandatory LoRA artifact sidecar (base_model_id is required)."""
    profile = get_profile(base_model_id) or {}
    out = Path(lora_path).with_suffix(".json")
    payload = {
        "subject_id": subject_id,
        "subject_name": subject_name,
        "trigger_word": trigger_word,
        "base_model_id": profile.get("id") or base_model_id,
        "lora_format": profile.get("lora_format"),
        "family": profile.get("family"),
        "train_backend": profile.get("train_backend"),
        "instance_prompt": f"a photo of {trigger_word}",
        "ref_count": ref_count,
        "steps": steps,
        "mock": bool(mock),
        "schema_version": 2,
    }
    if extra:
        payload.update(extra)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def resolve_inference_for_loras(lora_paths: list[str]) -> dict[str, Any]:
    """Given LoRA file paths, decide inference engine/model or raise on conflict.

    Returns dict: base_model_id, family, inference_engine, comfy_model_tag,
    offline_model_key, lora_format.
    """
    if not lora_paths:
        raise ValueError("no loras")
    bases: list[str] = []
    for lp in lora_paths:
        meta = read_lora_sidecar(lp)
        if meta and meta.get("base_model_id"):
            bases.append(str(meta["base_model_id"]))
        else:
            # Pre-registry LoRAs from the SDXL PEFT trainer
            bases.append(SDXL_LEGACY)
    uniq = []
    for b in bases:
        p = get_profile(b)
        bid = p["id"] if p else b
        if bid not in uniq:
            uniq.append(bid)
    if len(uniq) > 1:
        raise ValueError(
            f"Cannot mix LoRAs trained for different bases in one generate: {uniq}. "
            "Generate one character base at a time."
        )
    bid = uniq[0]
    p = get_profile(bid)
    if not p:
        raise ValueError(f"Unknown LoRA base_model_id: {bid}")
    return {
        "base_model_id": p["id"],
        "family": p["family"],
        "inference_engine": p["inference_engine"],
        "comfy_model_tag": p.get("comfy_model_tag"),
        "offline_model_key": p.get("offline_model_key"),
        "lora_format": p.get("lora_format"),
        "profile": p,
    }


def _lora_strength_settings() -> dict[str, float]:
    from backend.services.cast_lock import (
        DEFAULT_FLUX_DEV_STRENGTH,
        DEFAULT_SDXL_STRENGTH,
        DEFAULT_ZIMAGE_STRENGTH,
        SETTING_STRENGTH_FLUX,
        SETTING_STRENGTH_SDXL,
        SETTING_STRENGTH_ZIMAGE,
        resolve_lora_strength,
    )
    return {
        "zimage": resolve_lora_strength("zimage-turbo"),
        "sdxl": resolve_lora_strength("sdxl"),
        "flux": resolve_lora_strength("flux-dev"),
        "defaults": {
            "zimage": DEFAULT_ZIMAGE_STRENGTH,
            "sdxl": DEFAULT_SDXL_STRENGTH,
            "flux": DEFAULT_FLUX_DEV_STRENGTH,
        },
        "keys": {
            "zimage": SETTING_STRENGTH_ZIMAGE,
            "sdxl": SETTING_STRENGTH_SDXL,
            "flux": SETTING_STRENGTH_FLUX,
        },
    }


def set_character_lora_strength(family: str, value: float) -> float:
    """Persist operator override for character LoRA strength for a family."""
    from backend.services.cast_lock import (
        SETTING_STRENGTH_FLUX,
        SETTING_STRENGTH_SDXL,
        SETTING_STRENGTH_ZIMAGE,
        resolve_lora_strength,
    )
    from backend.models import SystemSetting, db

    fam = (family or "").lower().strip()
    key = {
        "zimage": SETTING_STRENGTH_ZIMAGE,
        "zimage-turbo": SETTING_STRENGTH_ZIMAGE,
        "sdxl": SETTING_STRENGTH_SDXL,
        "flux": SETTING_STRENGTH_FLUX,
        "flux-dev": SETTING_STRENGTH_FLUX,
    }.get(fam)
    if not key:
        raise ValueError(f"Unknown LoRA strength family: {family}")
    clamped = resolve_lora_strength(
        "zimage-turbo" if "zimage" in fam else ("flux-dev" if "flux" in fam else "sdxl"),
        float(value),
    )
    row = SystemSetting.query.filter_by(key=key).first()
    if row is None:
        row = SystemSetting(key=key, value=str(clamped))
        db.session.add(row)
    else:
        row.value = str(clamped)
    db.session.commit()
    return clamped


def public_settings_payload() -> dict[str, Any]:
    """JSON for Settings / Studio headers."""
    stills = get_stills_model_setting()
    train = get_cast_train_base_setting()
    maxq = get_max_quality_model_setting()
    return {
        "stills_model": stills,
        "cast_train_base": train,
        "max_quality_model": maxq,
        "character_lora_strength": _lora_strength_settings(),
        "defaults": {
            "stills_model": DEFAULT_STILLS_MODEL,
            "cast_train_base": DEFAULT_CAST_TRAIN_BASE,
            "max_quality_model": DEFAULT_MAX_QUALITY_MODEL,
        },
        "profiles": list_profiles(include_legacy=True),
        "train_profiles": list_profiles(role="lora_train", include_legacy=True),
        "stills_profiles": list_profiles(role="stills_t2i", include_legacy=True),
    }
