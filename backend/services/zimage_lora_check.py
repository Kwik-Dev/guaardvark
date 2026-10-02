"""Can the offline Z-Image engine load this LoRA file? Answered from key names alone.

The offline engine hands LoRAs to Diffusers, which reads plain LoRA pairs
(``lora_A``/``lora_B`` or ``lora_down``/``lora_up``) on Z-Image's own modules. Other
files circulate as "Z-Image LoRAs" too: LyCORIS formats (LoKr, LoHa), full
checkpoints, and adapters for other models. Diffusers fails on those deep inside its
converter with a list of every key it could not place. These checks say what the file
is in plain words, from the safetensors header (a few KB), so they can run before a
download and before a batch starts.
"""

from __future__ import annotations

import json
import logging
import re
import struct
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# One LoRA half on any module, in the Diffusers/PEFT, kohya or dotted spelling.
_ADAPTER_KEY = re.compile(r"(?:lora_(?:A|B|down|up)|lora\.(?:down|up))[._]")
# A module Z-Image has: the main layers and both refiners, in dotted or kohya spelling.
_ZIMAGE_MODULE = re.compile(
    r"(?:^|[._])(?:(?:layers|context_refiner|noise_refiner)[._]\d+[._]"
    r"(?:attention|feed_forward|adaLN_modulation)"
    r"|t_embedder|cap_embedder|all_x_embedder|all_final_layer)"
)
# Adapters for other models, told apart by block names Z-Image does not have. FLUX
# comes first: its kohya keys also contain img_mlp. The last field is what to do instead.
_OTHER_MODELS = (
    # FLUX.1 and FLUX.2 share these names; only FLUX.1 LoRAs stack here.
    (re.compile(r"double_blocks|single_blocks|single_transformer_blocks"), "a FLUX model",
     " If it is a FLUX.1 LoRA, add it again with FLUX.1 as the family."),
    (re.compile(r"img_mlp|txt_mlp|img_mod|txt_mod"), "Qwen-Image", ""),
    (re.compile(r"down_blocks|up_blocks|input_blocks|output_blocks|mid_block"),
     "Stable Diffusion or SDXL", " If it is an SDXL LoRA, add it again with SDXL as the family."),
    (re.compile(r"(?:^|[._])blocks[._]\d+[._]attn[._](?:wq|wk|wv|wo|gate)\b"), "Krea 2", ""),
    (re.compile(r"(?:^|[._])blocks[._]\d+[._](?:self_attn|cross_attn|ffn)"), "Wan", ""),
)
NOT_SAFETENSORS = (
    "It is not a .safetensors file, and the Z-Image engine loads LoRAs only in that format."
)


def zimage_lora_problem(keys: Iterable[str]) -> Optional[str]:
    """Why a file with these weight names cannot load as a Z-Image LoRA, or None if it can."""
    keys = [k for k in keys if k != "__metadata__"]
    if not keys:
        return "The file holds no weights."
    if any("lokr_w" in k for k in keys):
        return (
            "It is a LoKr adapter, a different format from LoRA that the Z-Image "
            "engine cannot load. Look for a LoRA version of it."
        )
    if any("hada_w" in k for k in keys):
        return (
            "It is a LoHa adapter, a different format from LoRA that the Z-Image "
            "engine cannot load. Look for a LoRA version of it."
        )
    adapter = [k for k in keys if _ADAPTER_KEY.search(k)]
    if not adapter:
        return (
            "It has no LoRA weights in it, so it is not a LoRA. It is most likely a "
            "full model checkpoint or another kind of file."
        )
    if any(_ZIMAGE_MODULE.search(k) for k in adapter):
        return None
    for pattern, label, advice in _OTHER_MODELS:
        if any(pattern.search(k) for k in adapter):
            return f"It is a LoRA for {label}, not Z-Image.{advice}"
    return "It is a LoRA for a different model: none of its weights match Z-Image's layers."


def safetensors_keys(path) -> list:
    """Tensor names from a local .safetensors header, without reading the weights."""
    with open(path, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            raise ValueError("file is shorter than a safetensors header")
        (size,) = struct.unpack("<Q", head)
        if not 0 < size <= 100 * 1024 * 1024:
            raise ValueError("no safetensors header")
        header = json.loads(f.read(size))
    return [k for k in header if k != "__metadata__"]


def lora_file_problem(path) -> Optional[str]:
    """``zimage_lora_problem`` for a file on disk. Unreadable files are left to the loader."""
    p = Path(path)
    if p.suffix.lower() != ".safetensors":
        return NOT_SAFETENSORS
    try:
        return zimage_lora_problem(safetensors_keys(p))
    except (OSError, ValueError) as e:
        logger.info("Could not read the LoRA header of %s: %s", p.name, e)
        return None


def hf_lora_problem(hf_repo: str, filename: str, revision: str = "main") -> Optional[str]:
    """``zimage_lora_problem`` for a file on the Hub, reading only its header.

    None when the file looks loadable and also when the header could not be fetched
    (gated repo, network): the batch-start and render checks still run on the
    downloaded file.
    """
    if not filename.lower().endswith(".safetensors"):
        return NOT_SAFETENSORS
    try:
        from huggingface_hub import HfApi

        meta = HfApi().parse_safetensors_file_metadata(hf_repo, filename, revision=revision)
    except Exception as e:  # noqa: BLE001 — an unread header is not a verdict
        logger.info("Could not read the LoRA header of %s/%s: %s", hf_repo, filename, e)
        return None
    return zimage_lora_problem(meta.tensors)


def plain_load_error(error: Exception) -> str:
    """A loader error without the full key list Diffusers puts in its message."""
    msg = str(error)
    listed = re.search(r"dict_keys\(\[(.*?)\]\)", msg, re.S)
    if listed:
        names = re.findall(r"'([^']+)'", listed.group(1))
        return (
            f"its layout is not one the Z-Image engine can read ({len(names)} weights "
            f"left unplaced, for example {', '.join(names[:3])})"
        )
    return msg if len(msg) <= 400 else msg[:400] + "..."
