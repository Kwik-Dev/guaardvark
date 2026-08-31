"""Declared families a person can add from a Hugging Face paste.

The Add Model dialogs match a repo against this table. A family that is not
wired is refused by name instead of being filed under SDXL or Wan. Sampling
floors and loaders stay on the shipped catalog entries named in ``like``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Weight names the inspector will list. GGUF is a ComfyUI UNET, not a
# diffusers snapshot, so it is in the union used for both domains.
WEIGHT_SUFFIXES = (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin")
_HF_HOST = re.compile(r"^https?://(www\.)?(huggingface\.co|hf\.co)/", re.I)
_HF_LIST_CAP = 200
_ENCODER_HINTS = (
    "text_encoder", "text-encoder", "qwen3vl", "qwen3-vl", "umt5", "t5xxl",
    "_t5_", "gemma", "llava", "clip_l", "/clip/",
)
_LORA_HINTS = ("/loras/", "lora", "lycoris", "adapter")
_HF_TOKEN_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")

# Filename fragments that mark a two-expert Wan UNET pair.
_HIGH_TOKS = ("highnoise", "high_noise", "high_lighting", "_high_")
_LOW_TOKS = ("lownoise", "low_noise", "low_lighting", "_low_")


class DuplicateUserModel(ValueError):
    """Same hf_repo + revision + files already sits in the user catalog."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(
            f"That Hugging Face file is already in your catalog as '{model_id}'."
        )


# ── family table ────────────────────────────────────────────────────────────
# wired=False rows are detectable so Look up can refuse them by name.
# roles / layout / engine tell the dialogs what to ask for; like is the shipped
# catalog id whose sampling policy and (for Comfy) graph the add clones.

IMAGE_FAMILIES = (
    {
        "id": "zimage",
        "domain": "image",
        "wired": True,
        "label": "Z-Image",
        "engine": "offline",
        "like": "zimage-turbo",
        "roles": ("generation", "lora"),
        "layout": "snapshot",
        "single_file": False,
        "lora_engine": "offline",
        "tokens": ("z-image", "zimage"),
        "pipeline_tags": (),
        "index_classes": ("ZImagePipeline", "ZImageImg2ImgPipeline"),
    },
    {
        "id": "krea2",
        "domain": "image",
        "wired": True,
        "label": "Krea 2",
        "engine": "offline",
        "like": "krea2-turbo",
        "roles": ("generation",),
        "layout": "snapshot",
        "single_file": False,
        "lora_engine": None,
        "tokens": ("krea",),
        "pipeline_tags": (),
        "index_classes": ("Krea2Pipeline",),
    },
    {
        "id": "sdxl",
        "domain": "image",
        "wired": True,
        "label": "SDXL",
        "engine": "offline",
        "like": "sd-xl",
        "roles": ("generation", "lora"),
        "layout": "snapshot",
        "single_file": True,
        "lora_engine": "comfy",
        "tokens": ("sdxl", "sd-xl", "juggernaut", "pony", "illustrious", "noobai"),
        "pipeline_tags": ("stable-diffusion-xl",),
        "index_classes": (
            "StableDiffusionXLPipeline",
            "StableDiffusionXLImg2ImgPipeline",
            "StableDiffusionXLInpaintPipeline",
        ),
    },
    {
        "id": "sd",
        "domain": "image",
        "wired": True,
        "label": "SD 1.5",
        "engine": "offline",
        "like": "realistic-vision",
        "roles": ("generation",),
        "layout": "single_file",
        "single_file": True,
        "lora_engine": None,
        "tokens": ("sd15", "sd-15", "sd1.5", "sd-1.5", "stable-diffusion-v1"),
        "pipeline_tags": ("stable-diffusion",),
        "index_classes": (
            "StableDiffusionPipeline",
            "StableDiffusionImg2ImgPipeline",
            "StableDiffusionInpaintPipeline",
        ),
    },
    {
        "id": "flux",
        "domain": "image",
        "wired": True,
        "label": "FLUX.1",
        "engine": "comfy",
        "like": "flux-dev",
        "roles": ("generation", "lora"),
        "layout": "comfy_files",
        "single_file": True,
        "lora_engine": "comfy",
        "tokens": ("flux", "flux.1", "flux1"),
        "pipeline_tags": ("flux",),
        "index_classes": ("FluxPipeline", "FluxImg2ImgPipeline", "FluxControlPipeline"),
    },
    {
        "id": "qwen-image-edit",
        "domain": "image",
        "wired": True,
        "label": "Qwen-Image-Edit",
        "engine": "comfy",
        "like": "qwen-image-edit",
        "roles": ("generation",),
        "layout": "comfy_files",
        "single_file": True,
        "lora_engine": None,
        "local_subdir": "diffusion_models",
        "tokens": ("qwen-image-edit", "qwenimage-edit", "qwen_image_edit"),
        "pipeline_tags": ("qwen-image-edit",),
        "index_classes": ("QwenImageEditPipeline", "QwenImageEditPlusPipeline"),
    },
)

UNWIRED_FAMILIES = (
    {
        "id": "qwen-image",
        "domain": "image",
        "wired": False,
        "label": "Qwen-Image",
        "tokens": ("qwen-image", "qwenimage", "qwen_image"),
        "pipeline_tags": ("qwen-image",),
        "index_classes": ("QwenImagePipeline",),
        "message": "Qwen-Image text-to-image is not wired yet — Qwen-Image-Edit (the Chat editor) is a different family.",
    },
    {
        "id": "sd3",
        "domain": "image",
        "wired": False,
        "label": "SD3 / SD3.5",
        "tokens": ("sd3.5", "sd3", "stable-diffusion-3"),
        "pipeline_tags": ("stable-diffusion-3",),
        "index_classes": ("StableDiffusion3Pipeline", "StableDiffusion3Img2ImgPipeline"),
        "message": "SD3 is not wired yet — this product cannot load that architecture.",
    },
    {
        "id": "hidream",
        "domain": "image",
        "wired": False,
        "label": "HiDream",
        "tokens": ("hidream",),
        "pipeline_tags": (),
        "index_classes": ("HiDreamImagePipeline",),
        "message": "HiDream is not wired yet — this product cannot load that architecture.",
    },
)

# Video add clones a shipped generation id (like). Tokens pick the type; extra
# tokens pick which shipped template. moe likes need a High + Low file.
VIDEO_FAMILIES = (
    {
        "id": "wan",
        "domain": "video",
        "wired": True,
        "label": "Wan",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("wan", "t2v", "i2v", "ti2v"),
        "likes": {
            "generation": (
                {"like": "wan22-14b-i2v", "any": ("i2v",), "moe": True},
                {"like": "wan22-5b", "any": ("5b", "ti2v"), "moe": False},
                {"like": "wan22-14b", "any": ("14b", "wan"), "moe": True},
            ),
            "lora": (
                {"like": "wan22-14b-i2v", "any": ("i2v",)},
                {"like": "wan22-14b", "any": ("wan", "t2v", "14b")},
            ),
            "encoder": (
                {"like": "wan22-5b", "any": ("umt5", "wan")},
            ),
        },
    },
    {
        "id": "minimax",
        "domain": "video",
        "wired": True,
        "label": "MiniMax H3",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("minimax", "h3", "hailuo"),
        "likes": {
            "generation": ({"like": "minimax-h3-int8", "any": ("minimax", "h3", "hailuo"), "moe": False},),
            "lora": ({"like": "minimax-h3-int8", "any": ("minimax", "h3")},),
            "encoder": ({"like": "minimax-h3-int8", "any": ("qwen3vl", "qwen3-vl", "minimax", "h3")},),
        },
    },
    {
        "id": "ltx",
        "domain": "video",
        "wired": True,
        "label": "LTX",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("ltx", "ltx2", "gemma"),
        "likes": {
            "generation": (
                {"like": "ltx25-distilled-int8", "any": ("2.5", "ltx25", "ltx-2.5"), "moe": False},
                {"like": "ltx23-distilled-fp8", "any": ("ltx",), "moe": False},
            ),
            "lora": ({"like": "ltx25-distilled-int8", "any": ("2.5", "ltx25")}, {"like": "ltx23-distilled-fp8", "any": ("ltx",)}),
            "encoder": (
                {"like": "ltx25-distilled-int8", "any": ("gemma4", "gemma-4", "ltx-2.5", "ltx25")},
                {"like": "ltx23-distilled-fp8", "any": ("gemma", "ltx")},
            ),
        },
    },
    {
        "id": "hunyuan",
        "domain": "video",
        "wired": True,
        "label": "Hunyuan",
        "roles": ("generation", "lora", "encoder"),
        "tokens": ("hunyuan",),
        "likes": {
            "generation": (
                {"like": "hunyuan-i2v", "any": ("i2v",), "moe": False},
                {"like": "hunyuan-t2v", "any": ("hunyuan",), "moe": False},
            ),
            "lora": ({"like": "hunyuan-t2v", "any": ("hunyuan",)},),
            "encoder": ({"like": "hunyuan-t2v", "any": ("llava", "hunyuan")},),
        },
    },
    {
        "id": "cogvideox",
        "domain": "video",
        "wired": True,
        "label": "CogVideoX",
        "roles": ("generation",),
        "tokens": ("cogvideox", "cogvideo"),
        "likes": {
            "generation": (
                {"like": "cogvideox-5b-i2v", "any": ("i2v",), "moe": False},
                {"like": "cogvideox-5b", "any": ("cogvideo",), "moe": False},
            ),
        },
    },
)


def hf_token_present() -> bool:
    return any(os.environ.get(k) for k in _HF_TOKEN_ENV)


def image_generation_families() -> dict:
    """Shape consumed by user_image_models.build_user_entry."""
    out = {}
    for row in IMAGE_FAMILIES:
        if not row["wired"]:
            continue
        out[row["id"]] = {
            "label": row["label"],
            "like": row["like"],
            "single_file": bool(row["single_file"]),
            "engine": row["engine"],
            "lora": "lora" in row["roles"],
            "lora_engine": row.get("lora_engine"),
            "layout": row["layout"],
            "local_subdir": row.get("local_subdir"),
        }
    return out


def image_lora_families() -> tuple:
    return tuple(
        row["id"] for row in IMAGE_FAMILIES
        if row["wired"] and "lora" in row["roles"]
    )


def family_choices(domain: str = "image") -> list:
    if domain != "image":
        return []
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "like": row["like"],
            "single_file": bool(row["single_file"]),
            "lora": "lora" in row["roles"],
            "engine": row["engine"],
        }
        for row in IMAGE_FAMILIES
        if row["wired"]
    ]


def hf_inspect_url(hf_repo: str, revision: str = "main", src: str | None = None) -> str:
    """A huggingface.co URL inspect_hf_repo can parse from org/repo pieces."""
    repo = (hf_repo or "").strip()
    rev = (revision or "main").strip() or "main"
    if src:
        return f"https://huggingface.co/{repo}/blob/{rev}/{src}"
    if rev != "main":
        return f"https://huggingface.co/{repo}/tree/{rev}"
    return f"https://huggingface.co/{repo}"


def parse_hf_url(url: str) -> dict:
    """Turn a paste into {hf_repo, revision, src}.

    Accepts huggingface.co and hf.co, /blob|/resolve|/tree/{rev}/{path},
    and a bare org/repo. Query strings and trailing slashes are stripped.
    Dataset and Space URLs are refused — they are not model repos.
    ``refs/pr/N`` and ``refs/heads/…`` are kept as the revision.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Paste a Hugging Face URL or org/repo.")
    raw = raw.split("?")[0].split("#")[0].rstrip("/")
    raw = _HF_HOST.sub("", raw)
    if "://" in raw or raw.lower().startswith("www."):
        raise ValueError("Only Hugging Face URLs or org/repo ids are accepted.")
    parts = [p for p in raw.split("/") if p]
    if parts and parts[0].lower() in ("datasets", "spaces"):
        kind = "Space" if parts[0].lower() == "spaces" else "dataset"
        raise ValueError(
            f"That is a Hugging Face {kind}, not a model repo. "
            "Paste a model URL (huggingface.co/org/repo)."
        )
    if parts and parts[0].lower() == "models" and len(parts) >= 3:
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("Need org/repo (for example Comfy-Org/MiniMax-H3).")
    repo = f"{parts[0]}/{parts[1]}"
    src = None
    revision = "main"
    if len(parts) >= 4 and parts[2] in ("blob", "resolve", "tree"):
        revision, src = _revision_and_src(parts[3:])
    elif len(parts) > 2:
        src = "/".join(parts[2:])
    if src:
        src = sanitize_repo_src(src)
    return {"hf_repo": repo, "revision": revision, "src": src}


def _revision_and_src(rest: list[str]) -> tuple[str, str | None]:
    """Split blob/resolve/tree remainder into revision and optional file path."""
    if not rest:
        return "main", None
    if rest[0] == "refs" and len(rest) >= 3:
        revision = "/".join(rest[:3])
        src = "/".join(rest[3:]) or None
        return revision, src
    revision = rest[0] or "main"
    src = "/".join(rest[1:]) or None
    return revision, src


def sanitize_repo_src(src: str) -> str:
    """A path inside the repo. Rejects empty, absolute, and parent-directory parts."""
    s = (src or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        raise ValueError("File path must be a file inside the repo.")
    parts = [p for p in s.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        raise ValueError("File path must be a file inside the repo.")
    if s.startswith("/") or Path(s).is_absolute():
        raise ValueError("File path must be a file inside the repo.")
    return "/".join(parts)


def list_hf_weight_files(repo_id: str, revision: str = "main") -> tuple[list, bool, bool]:
    """Weight files (name + size). Caps at _HF_LIST_CAP. Returns (files, gated, truncated)."""
    from huggingface_hub import HfApi

    api = HfApi()
    info = api.repo_info(repo_id=repo_id, revision=revision, files_metadata=True)
    gated = bool(getattr(info, "gated", False))
    files = []
    truncated = False
    for sib in info.siblings or []:
        name = getattr(sib, "rfilename", None) or ""
        if not name.lower().endswith(WEIGHT_SUFFIXES):
            continue
        files.append({"src": name, "size": int(getattr(sib, "size", 0) or 0)})
        if len(files) >= _HF_LIST_CAP:
            truncated = True
            break
    return files, gated, truncated


def _license_from_info(info) -> str | None:
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None)
    if isinstance(card, dict):
        lic = card.get("license") or card.get("licence")
        if lic:
            return str(lic)
    if card is not None:
        lic = getattr(card, "license", None) or (card.get("license") if hasattr(card, "get") else None)
        if lic:
            return str(lic)
    lic = getattr(info, "license", None)
    return str(lic) if lic else None


def inspect_hf_repo(url: str) -> dict:
    """Parse a paste and read the repo. Does not download weights."""
    from huggingface_hub import HfApi

    parsed = parse_hf_url(url)
    repo, revision = parsed["hf_repo"], parsed.get("revision") or "main"
    files, gated, truncated = list_hf_weight_files(repo, revision)
    src = parsed.get("src")
    if src and not any(f["src"] == src for f in files):
        files.insert(0, {"src": src, "size": 0})
    has_model_index = False
    pipeline_tag = None
    license_id = None
    try:
        api = HfApi()
        info = api.model_info(repo, revision=revision)
        pipeline_tag = getattr(info, "pipeline_tag", None) or None
        license_id = _license_from_info(info)
        gated = gated or bool(getattr(info, "gated", False))
    except Exception:  # noqa: BLE001 — listing already succeeded
        info = None
    try:
        all_files = HfApi().list_repo_files(repo, revision=revision)
        has_model_index = "model_index.json" in all_files
    except Exception:  # noqa: BLE001
        all_files = []
    index_class = _read_model_index_class(repo, revision) if has_model_index else None
    return {
        "hf_repo": repo,
        "revision": revision,
        "src": src,
        "files": files,
        "gated": gated,
        "truncated": truncated,
        "has_model_index": has_model_index,
        "pipeline_tag": pipeline_tag,
        "index_class": index_class,
        "license": license_id,
        "token_present": hf_token_present(),
        "warnings": _inspect_warnings(files, has_model_index),
    }


def _class_stem(name: str | None) -> str:
    return (name or "").rsplit(".", 1)[-1].strip()


def _read_model_index_class(repo_id: str, revision: str = "main") -> str | None:
    """``_class_name`` from model_index.json. Small JSON only; never weights."""
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(
            repo_id=repo_id, filename="model_index.json", revision=revision,
        )
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — listing already succeeded; class is a hint
        return None
    if not isinstance(data, dict):
        return None
    return _class_stem(data.get("_class_name")) or None


def _inspect_warnings(files: list, has_model_index: bool) -> list:
    warnings = []
    names = " ".join(f.get("src") or "" for f in files).lower()
    if any("-of-" in (f.get("src") or "").lower() for f in files) and not has_model_index:
        warnings.append(
            "This repo has sharded weights. Pick a merged .safetensors, or a diffusers repo root."
        )
    if any(n.endswith(".gguf") for n in names.split()) and has_model_index:
        warnings.append("Diffusers repo with GGUF files — pick the GGUF as a ComfyUI UNET, or snapshot the repo for an offline family.")
    return warnings


def _blob(files: list, src: str | None, hf_repo: str) -> str:
    names = [src] if src else [f.get("src") or "" for f in files]
    return " ".join(names + [hf_repo or ""]).lower()


def _has_moe_pair(files: list, src: str | None) -> bool:
    names = [src] if src else [f.get("src") or "" for f in files]
    has_high = any(any(tok in n.lower() for tok in _HIGH_TOKS) for n in names)
    has_low = any(any(tok in n.lower() for tok in _LOW_TOKS) for n in names)
    return has_high and has_low


def _guess_role(blob: str, domain: str) -> str:
    if any(k in blob for k in _LORA_HINTS):
        return "lora"
    if domain == "video" and any(k in blob for k in _ENCODER_HINTS):
        return "encoder"
    return "generation"


def _pick_like(rules: tuple, blob: str) -> dict | None:
    for rule in rules:
        any_toks = rule.get("any") or ()
        if not any_toks or any(t in blob for t in any_toks):
            return rule
    return rules[0] if rules else None


def match_families(
    *,
    domain: str,
    files: list,
    src: str | None,
    hf_repo: str,
    has_model_index: bool = False,
    pipeline_tag: str | None = None,
    index_class: str | None = None,
) -> list[dict]:
    """Rank family matches. Unwired hits are returned alone so the UI refuses them."""
    blob = _blob(files, src, hf_repo)
    tag = (pipeline_tag or "").lower().replace("_", "-")
    cls = _class_stem(index_class)
    unwired = []
    for row in UNWIRED_FAMILIES:
        if row["domain"] != domain:
            continue
        hit = (
            any(t in blob for t in row["tokens"])
            or tag in {t.lower() for t in row.get("pipeline_tags") or ()}
            or (cls and cls in (row.get("index_classes") or ()))
        )
        if row["id"] == "qwen-image" and (
            "edit" in blob or (cls and "edit" in cls.lower())
        ):
            # Qwen-Image-Edit is a wired family; do not refuse it as unwired t2i.
            continue
        if hit:
            unwired.append({
                "family": row["id"],
                "label": row["label"],
                "role": "generation",
                "like": None,
                "wired": False,
                "confidence": "high",
                "reason": row["message"],
                "moe": False,
                "engine": None,
            })
    if unwired:
        return unwired

    role = _guess_role(blob, domain)
    matches = []

    if domain == "image":
        for row in IMAGE_FAMILIES:
            if not row["wired"]:
                continue
            score = 0
            reasons = []
            for tok in row["tokens"]:
                if tok in blob:
                    score += 3 if tok not in ("xl",) else 1
                    reasons.append(tok)
            if tag and tag in {t.lower() for t in row.get("pipeline_tags") or ()}:
                score += 4
                reasons.append(f"pipeline:{tag}")
            if cls and cls in (row.get("index_classes") or ()):
                score += 5
                reasons.append(f"class:{cls}")
            # "xl" in SDXL filenames is a weaker signal; score it only if no
            # stronger family already claimed the blob.
            if row["id"] == "sdxl" and "xl" in blob and score == 0:
                score += 1
                reasons.append("xl")
            if row["id"] == "sd" and has_model_index and score == 0 and role == "generation":
                # Fallback for a diffusers repo with no family tokens.
                score += 1
                reasons.append("diffusers")
            if row["id"] == "zimage" and role == "lora" and score == 0:
                score += 1
                reasons.append("lora-default")
            if role not in row["roles"]:
                continue
            if score <= 0:
                continue
            matches.append({
                "family": row["id"],
                "label": row["label"],
                "role": role if role in row["roles"] else "generation",
                "like": row["like"],
                "wired": True,
                "confidence": "high" if score >= 3 else "low",
                "reason": ", ".join(reasons) or row["id"],
                "moe": False,
                "engine": row["engine"],
                "score": score,
            })
        if not matches and role == "generation" and has_model_index:
            sd = next(r for r in IMAGE_FAMILIES if r["id"] == "sd")
            matches.append({
                "family": "sd",
                "label": sd["label"],
                "role": "generation",
                "like": sd["like"],
                "wired": True,
                "confidence": "low",
                "reason": "diffusers repo",
                "moe": False,
                "engine": sd["engine"],
                "score": 1,
            })
        if not matches and role == "lora":
            zi = next(r for r in IMAGE_FAMILIES if r["id"] == "zimage")
            matches.append({
                "family": "zimage",
                "label": zi["label"],
                "role": "lora",
                "like": zi["like"],
                "wired": True,
                "confidence": "low",
                "reason": "LoRA default",
                "moe": False,
                "engine": zi["engine"],
                "score": 1,
            })

    elif domain == "video":
        moe_pair = _has_moe_pair(files, src)
        for row in VIDEO_FAMILIES:
            if role not in row["roles"]:
                continue
            type_hit = any(t in blob for t in row["tokens"])
            rules = (row.get("likes") or {}).get(role) or ()
            picked = _pick_like(rules, blob) if (type_hit or rules) else None
            if not picked:
                continue
            # Skip types that did not actually appear in the paste unless the
            # like-rule's tokens did (encoder hints often name the type).
            if not type_hit and not any(t in blob for t in (picked.get("any") or ())):
                continue
            score = 3 if type_hit else 2
            matches.append({
                "family": row["id"],
                "label": row["label"],
                "role": role,
                "like": picked["like"],
                "wired": True,
                "confidence": "high" if type_hit else "low",
                "reason": row["id"],
                "moe": bool(picked.get("moe")) or (moe_pair and row["id"] == "wan"),
                "engine": "comfy",
                "score": score + (1 if moe_pair and picked.get("moe") else 0),
            })
        if moe_pair and not any(m["family"] == "wan" for m in matches):
            matches.append({
                "family": "wan",
                "label": "Wan",
                "role": "generation",
                "like": "wan22-14b-i2v" if "i2v" in blob else "wan22-14b",
                "wired": True,
                "confidence": "high",
                "reason": "HighNoise + LowNoise pair",
                "moe": True,
                "engine": "comfy",
                "score": 4,
            })

    matches.sort(key=lambda m: -int(m.get("score") or 0))
    for m in matches:
        m.pop("score", None)
    return matches


def find_duplicate(catalog: dict, *, hf_repo: str, revision: str, files: list, kind: str | None = None) -> str | None:
    """Existing user-catalog id with the same repo + revision + file srcs, or None."""
    models = (catalog or {}).get("models") or {}
    wanted = tuple(sorted(
        (item.get("src") if isinstance(item, dict) else "") or ""
        for item in (files or [])
        if (item.get("src") if isinstance(item, dict) else item)
    ))
    rev = revision or "main"
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if (entry.get("hf_repo") or "") != hf_repo:
            continue
        if (entry.get("revision") or "main") != rev:
            continue
        have = tuple(sorted(f.get("src") or "" for f in (entry.get("files") or []) if f.get("src")))
        if kind == "snapshot" and entry.get("kind") == "snapshot":
            return mid
        if wanted and have == wanted:
            return mid
        if not wanted and not have and entry.get("kind") == "snapshot":
            return mid
    return None
