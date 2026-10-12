"""How many prompt tokens an image model reads, and how many a prompt uses.

The offline Z-Image and Krea 2 pipelines encode a fixed number of tokens and
drop the rest without a warning. The limit is declared on the family row
(``max_prompt_tokens`` in media_model_registry.IMAGE_FAMILY_SPECS); this module
counts a prompt the way each pipeline tokenizes it, so the Studio's counter,
the enhancement budget and the render agree on one number.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

from backend.services.image_render_limits import limits_for

logger = logging.getLogger(__name__)

# Krea2Pipeline encodes the prompt plus "<|im_end|>\n<|im_start|>assistant\n"
# (5 tokens) inside its max_sequence_length; its system prefix sits outside it.
_KREA2_SUFFIX_TOKENS = 5

_tokenizers: dict[str, Any] = {}
_tokenizers_lock = threading.Lock()


def max_prompt_tokens(model_or_family: Optional[str]) -> Optional[int]:
    """The declared cap for a catalog key, HF id or family name, else None."""
    cap = limits_for(model_or_family).get("max_prompt_tokens")
    return int(cap) if cap else None


def count_prompt_tokens(family: str, prompt: str, tokenizer: Any) -> int:
    """Tokens ``prompt`` takes out of the family's cap, counted the way its
    diffusers pipeline builds the encoder input."""
    if family == "zimage":
        # ZImagePipeline._encode_prompt wraps the prompt in the chat template
        # and truncates the whole templated string.
        templated = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=True,
        )
        return len(tokenizer(templated).input_ids)
    if family == "krea2":
        return len(tokenizer(prompt).input_ids) + _KREA2_SUFFIX_TOKENS
    return len(tokenizer(prompt).input_ids)


def estimate_prompt_tokens(prompt: str) -> int:
    """Prose estimate when no tokenizer is at hand (~1.5 tokens/word; measured
    1.2-1.34 on real long prompts, so it errs toward the limit)."""
    return int(len(prompt.split()) * 1.5) + 1


def load_tokenizer(model_dir: Path) -> Any:
    """The tokenizer shipped in ``<model_dir>/tokenizer``, cached per folder;
    None when the model is not on disk."""
    key = str(model_dir)
    with _tokenizers_lock:
        if key in _tokenizers:
            return _tokenizers[key]
    tok_dir = Path(model_dir) / "tokenizer"
    if not tok_dir.is_dir():
        return None
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(tok_dir))
    with _tokenizers_lock:
        _tokenizers[key] = tokenizer
    return tokenizer
