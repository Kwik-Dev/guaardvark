"""Kokoro TTS backend (hexgrad/Kokoro-82M, Apache 2.0).

Lightweight fallback TTS — ~80M params, sub-1 GB VRAM, fast. Several
built-in voices but no reference-clip cloning. Used when Chatterbox fails
to load (OOM) or when the caller explicitly asks for backend="kokoro".

Heavy imports live inside methods.

Install (handled at first start.sh run after the requirements.txt bump):
    pip install kokoro

Everything a generation reads comes from this machine: the model files and
voice packs from the Hugging Face cache (resolved to local paths here, so
kokoro's own hf_hub_download is never reached), and spaCy's English pipeline
from the plugin venv. A missing piece raises WeightsNotInstalled naming Audio
Studio → Manage models, whose Install fetches all of it. The voice list is
backends/kokoro_voices.json.
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

from backends import kokoro_voices
from backends.base import AudioBackend, GenerationResult
from backends.hub_weights import (
    INSTALL_HINT,
    WeightsNotInstalled,
    cached_hub_file,
    require_hub_files,
)

logger = logging.getLogger(__name__)


class KokoroBackend(AudioBackend):
    """Kokoro-82M TTS — 24 kHz mono, built-in voices.

    Voice IDs are prefixed with the accent: af_*/am_* are American English,
    bf_*/bm_* are British English, ef_*/em_* are Spanish. The phonemizer
    (which is what the lang_code controls) needs to match the voice's accent
    or pronunciation gets weird — we keep one KPipeline per lang_code and
    route at generate-time.
    """

    name = "kokoro"
    vram_mb_estimate = 600  # ~500 MB observed; pad for activations

    # Recognized accent prefixes -> Kokoro lang_code.
    # Voice IDs that don't start with these fall back to American.
    # Spanish ("e") needs the misaki[es] extra in the venv (see requirements.txt)
    # and espeak-ng on the host. Extend with f/h/i/j/p/z when wiring more langs.
    _ACCENT_LANG_CODES = {"a": "a", "b": "b", "e": "e"}

    def __init__(
        self,
        output_root: Path,
        sample_rate: int = 24000,
        default_voice: str = "af_heart",
    ) -> None:
        self._output_root = Path(output_root)
        self._sample_rate = int(sample_rate)
        self._default_voice = default_voice
        # One pipeline per lang_code, lazy-loaded. The "default" one (American)
        # is created at load() time; British is created on first British voice.
        self._pipelines: dict[str, Any] = {}

    @property
    def is_loaded(self) -> bool:
        return bool(self._pipelines)

    @classmethod
    def _lang_code_for(cls, voice_id: str) -> str:
        """Return the Kokoro lang_code matching this voice's accent prefix."""
        if not voice_id:
            return "a"
        prefix = voice_id[0].lower()
        return cls._ACCENT_LANG_CODES.get(prefix, "a")

    def _model_paths(self) -> dict[str, str]:
        """Local paths of config.json and the weights, or WeightsNotInstalled."""
        return require_hub_files(
            kokoro_voices.hf_repo(), kokoro_voices.model_files(), "Kokoro voice",
        )

    @staticmethod
    def _require_english_g2p(lang_code: str) -> None:
        """Refuse before misaki would pip-install spaCy's English pipeline."""
        g2p = kokoro_voices.english_g2p()
        if lang_code not in g2p["lang_codes"]:
            return
        from importlib import metadata

        try:
            # The same test misaki runs (spacy.util.is_package) before it
            # falls back to spacy.cli.download.
            metadata.distribution(g2p["package"])
        except metadata.PackageNotFoundError:
            raise WeightsNotInstalled(
                f"Kokoro English voices need spaCy's {g2p['package']} language "
                f"model, which is not installed in the Audio Foundry environment. "
                f"{INSTALL_HINT}"
            ) from None

    def _voice_path(self, voice_id: str) -> str:
        """Local path of a catalog voice pack.

        Passing KPipeline a path (it loads anything ending in .pt directly)
        instead of the id keeps it from calling hf_hub_download. Ids outside
        the catalog are refused here, so a caller cannot hand it an arbitrary
        file or a comma-separated blend.
        """
        voice_id = kokoro_voices.check_voice_id(voice_id)
        path = cached_hub_file(kokoro_voices.hf_repo(), kokoro_voices.voice_file(voice_id))
        if path is None:
            raise WeightsNotInstalled(
                f"Kokoro voice '{voice_id}' is not on this machine. {INSTALL_HINT}"
            )
        if "," in path:
            # KPipeline.load_voice splits on commas; a cache under such a
            # directory would be read as a blend of partial paths.
            raise WeightsNotInstalled(
                f"Kokoro voice '{voice_id}': the Hugging Face cache path contains a "
                f"comma ({path}), which Kokoro cannot load. Move HF_HOME."
            )
        return path

    def _build_pipeline(self, lang_code: str, device: str) -> Any:
        from kokoro import KModel, KPipeline

        paths = self._model_paths()
        repo = kokoro_voices.hf_repo()
        model = KModel(
            repo_id=repo,
            config=paths[kokoro_voices.config_file()],
            model=paths[kokoro_voices.weights_file()],
        ).to(device).eval()
        return KPipeline(lang_code=lang_code, repo_id=repo, model=model)

    def _get_or_load_pipeline(self, lang_code: str) -> Any:
        """Return the KPipeline for this lang_code, loading it if needed."""
        if lang_code in self._pipelines:
            return self._pipelines[lang_code]

        try:
            import kokoro  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "kokoro package not installed. Run: pip install kokoro"
            ) from e
        import torch

        # Both checks raise before any model memory is allocated.
        self._model_paths()
        self._require_english_g2p(lang_code)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading Kokoro pipeline for lang_code=%r on %s", lang_code, device)
        try:
            pipeline = self._build_pipeline(lang_code, device)
        except RuntimeError as e:
            if device != "cuda" or "CUDA" not in str(e):
                raise
            # Kokoro-82M is tiny — when a render owns the card (observed:
            # 12.4GB ComfyUI job left 39MB free), narration must not die.
            # CPU synthesis is near-realtime for this model.
            logger.warning(
                "Kokoro CUDA init failed (%s) — retrying on CPU", e)
            pipeline = self._build_pipeline(lang_code, "cpu")
        self._pipelines[lang_code] = pipeline
        return pipeline

    def load(self) -> None:
        # Pre-warm only the default voice's lang_code. Other accents lazy-load
        # on first request — keeps cold-start fast and VRAM low.
        if self._pipelines:
            return
        logger.info("Loading Kokoro-82M from local cache...")
        self._get_or_load_pipeline(self._lang_code_for(self._default_voice))
        logger.info("Kokoro loaded")

    def unload(self) -> None:
        if not self._pipelines:
            return
        import torch

        self._pipelines.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info("Kokoro unloaded")

    def generate(self, **params: Any) -> GenerationResult:
        if not self._pipelines:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        requested_format = params.get("output_format", "wav")
        # Kokoro has no reference-clip cloning — silently ignore those args.

        voice = kokoro_voices.check_voice_id(voice)
        voice_path = self._voice_path(voice)

        # Route to the right phonemizer for this voice's accent. American voices
        # speak Kokoro's American pipeline; British speak its British pipeline.
        lang_code = self._lang_code_for(voice)
        pipeline = self._get_or_load_pipeline(lang_code)

        import numpy as np
        import soundfile as sf

        logger.info("Kokoro generate: chars=%d voice=%s lang=%s", len(text), voice, lang_code)
        t0 = time.monotonic()

        # KPipeline streams tuples: (graphemes, phonemes, audio_tensor).
        # We concatenate all audio chunks; each is a 1-D float tensor at 24 kHz.
        segments = []
        for _, _, audio_tensor in pipeline(text, voice=voice_path):
            arr = audio_tensor.cpu().numpy() if hasattr(audio_tensor, "cpu") else np.asarray(audio_tensor)
            segments.append(arr)
        gen_seconds = time.monotonic() - t0

        if not segments:
            raise RuntimeError("Kokoro produced no audio segments — input may be empty")

        audio = np.concatenate(segments)

        self._output_root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid.uuid4().hex
        out_path = self._output_root / f"{asset_id}.wav"
        sf.write(str(out_path), audio, self._sample_rate)

        # Reap the raw WAV if post_process fails or replaces it (see chatterbox).
        try:
            final_path = self.post_process(out_path, output_format=requested_format)
        except Exception:
            out_path.unlink(missing_ok=True)
            raise
        if final_path != out_path:
            out_path.unlink(missing_ok=True)
        actual_format = final_path.suffix.lstrip(".").lower()

        actual_duration = audio.shape[0] / self._sample_rate
        logger.info(
            "Kokoro wrote %s — %.2fs audio in %.1fs wall",
            final_path, actual_duration, gen_seconds,
        )

        return GenerationResult(
            path=final_path.resolve(),
            duration_s=actual_duration,
            sample_rate=self._sample_rate,
            meta={
                "backend": self.name,
                "text": text,
                "voice": voice,
                "requested_output_format": requested_format,
                "actual_output_format": actual_format,
                "generation_seconds": round(gen_seconds, 2),
            },
        )

    def stream(self, **params: Any):
        """Yield (wav_chunk_bytes, is_first) tuples for sentence-by-sentence streaming playback.

        Per voice specialist audit: this enables first audible speech after the first Kokoro
        iteration (~hundreds of ms for short sentence) instead of waiting for the entire
        text to be synthesized and written to a single file.

        Each yielded chunk is a complete small WAV (with its own header) so it can be
        played immediately in <audio> or concatenated client-side. Cross-sentence joins
        may need crossfade for smoothness.
        """
        if not self._pipelines:
            raise RuntimeError("Kokoro not loaded; call load() first")

        text: str = params["text"]
        voice = params.get("voice_id") or self._default_voice
        voice = kokoro_voices.check_voice_id(voice)
        voice_path = self._voice_path(voice)
        lang_code = self._lang_code_for(voice)
        pipeline = self._get_or_load_pipeline(lang_code)

        import numpy as np
        import soundfile as sf
        import io

        first = True
        for _, _, audio_tensor in pipeline(text, voice=voice_path):
            arr = audio_tensor.cpu().numpy() if hasattr(audio_tensor, "cpu") else np.asarray(audio_tensor)
            buf = io.BytesIO()
            sf.write(buf, arr, self._sample_rate, format='WAV')
            buf.seek(0)
            chunk = buf.read()
            yield chunk, first
            first = False
