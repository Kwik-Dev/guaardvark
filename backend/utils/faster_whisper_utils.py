"""
Faster-Whisper transcription utility.

Uses CTranslate2-based faster-whisper for ~4x faster transcription
vs whisper.cpp subprocess calls. Models are cached globally to avoid
reload overhead on each request.

Safe import: if faster-whisper is not installed, this module loads
but functions raise ImportError on use.

Loading never downloads. Weights reach the Hugging Face cache only through
install_model(), which the voice model Install calls; a missing model raises
SpeechModelMissing, whose message is what the voice clients show.
"""

import os
import time
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False
    WhisperModel = None
    download_model = None

SPEECH_MODEL_MISSING_MESSAGE = "Install the speech model to use voice"

# Global cache for the loaded model
_whisper_model = None
_current_model_size = None


class SpeechModelMissing(RuntimeError):
    """The weights for a model size are not on this machine."""

    def __init__(self, model_size: str):
        super().__init__(SPEECH_MODEL_MISSING_MESSAGE)
        self.model_size = model_size


def local_model_path(model_size: str) -> Optional[str]:
    """Folder holding the complete weights for model_size, or None. No network."""
    if not FASTER_WHISPER_AVAILABLE:
        return None
    try:
        path = download_model(model_size, local_files_only=True)
    except FileNotFoundError:
        # huggingface_hub's LocalEntryNotFoundError (nothing cached) and
        # IncompleteSnapshotError (an interrupted download) are both this.
        return None
    if not os.path.isfile(os.path.join(path, "model.bin")):
        return None
    return path


def is_model_installed(model_size: str) -> bool:
    return local_model_path(model_size) is not None


def install_model(model_size: str) -> str:
    """Download the weights for model_size into the Hugging Face cache.

    The only call in this module that reaches the network. It belongs behind
    a visible Install; transcription paths use get_faster_whisper_model().
    """
    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")
    return download_model(model_size)


def weights_cache_dir(model_size: str) -> Optional[str]:
    """Hugging Face cache folder install_model() writes model_size into, or None.

    Only used to show download progress, so an unknown layout returns None
    rather than raising.
    """
    try:
        from faster_whisper.utils import _MODELS
        from huggingface_hub import constants
    except ImportError:
        return None
    repo_id = _MODELS.get(model_size)
    if not repo_id:
        return None
    return os.path.join(constants.HF_HUB_CACHE, "models--" + repo_id.replace("/", "--"))


def get_faster_whisper_model(
    model_size: str = "tiny.en",
    device: str = "auto",
    compute_type: str = "int8"
) -> "WhisperModel":
    """Get or create a cached faster-whisper model instance."""
    global _whisper_model, _current_model_size

    if not FASTER_WHISPER_AVAILABLE:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")

    if _whisper_model is not None and _current_model_size == model_size:
        return _whisper_model

    if local_model_path(model_size) is None:
        raise SpeechModelMissing(model_size)

    logger.info(f"Loading faster-whisper model '{model_size}' (device={device}, compute_type={compute_type})")
    start = time.time()

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type,
                             local_files_only=True)
    except Exception:
        if compute_type != "int8":
            logger.info("Falling back to compute_type='int8'")
            model = WhisperModel(model_size, device=device, compute_type="int8",
                                 local_files_only=True)
        else:
            raise

    _whisper_model = model
    _current_model_size = model_size
    logger.info(f"Loaded faster-whisper '{model_size}' in {time.time() - start:.2f}s")
    return model


def is_loaded() -> bool:
    return _whisper_model is not None


def unload() -> bool:
    """Release the cached model. Returns True if something was released.

    device="auto" resolves to CUDA wherever CTranslate2 can see the card, so a
    single voice message leaves an encoder/decoder and a cuBLAS workspace resident
    for the life of the process. Only a request for a *different* model_size ever
    replaced it, and only "tiny.en" is ever asked for. Never raises.
    """
    global _whisper_model, _current_model_size
    if _whisper_model is None:
        return False
    _whisper_model = None
    _current_model_size = None
    try:
        import gc
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass
    logger.info("faster-whisper model unloaded")
    return True


def transcribe_audio_faster(audio_input, model_size: str = "tiny.en") -> Tuple[str, float]:
    """Transcribe an audio file or numpy array using faster-whisper.

    Args:
        audio_input: Path to audio file (WAV, MP3, etc.) or numpy array of audio data
        model_size: Whisper model size (tiny.en, base, small, etc.)

    Returns:
        Tuple of (transcribed_text, processing_time_seconds)
    """
    model = get_faster_whisper_model(model_size=model_size)
    start = time.time()

    segments, info = model.transcribe(
        audio_input,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    text = " ".join(segment.text for segment in segments)
    duration = time.time() - start

    return text.strip(), duration
