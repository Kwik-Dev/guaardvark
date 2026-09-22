"""Opt-in voice backend selection (voice.backend).

Default is Guaardvark's own Audio Foundry; "pi-omni" routes through the external
OpenAI-compatible router and falls back to the Guaardvark API when unreachable.
"""
import pytest
from unittest.mock import AsyncMock

from core.api_client import APIError
from core.voice_handler import VoiceHandler


def _handler(backend=None):
    api = AsyncMock()
    voice = {"tts_voice": "ryan", "router_tts_voice": "af_heart"}
    if backend is not None:
        voice["backend"] = backend
    handler = VoiceHandler(api, {"voice": voice})
    handler._play_audio = AsyncMock()
    return handler, api


def test_backend_defaults_to_guaardvark():
    handler, _ = _handler()
    assert handler._voice_backend() == "guaardvark"


def test_backend_pi_omni_aliases():
    for value in ("pi-omni", "PI_OMNI", "router", "openai"):
        handler, _ = _handler(value)
        assert handler._voice_backend() == "pi-omni"


@pytest.mark.asyncio
async def test_transcribe_default_uses_guaardvark_api():
    handler, api = _handler()
    api.speech_to_text.return_value = {"text": "hello"}

    assert await handler._transcribe(b"wav") == "hello"
    api.speech_to_text_router.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_pi_omni_uses_router():
    handler, api = _handler("pi-omni")
    api.speech_to_text_router.return_value = "hello"

    assert await handler._transcribe(b"wav") == "hello"
    api.speech_to_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcribe_falls_back_when_router_unreachable():
    handler, api = _handler("pi-omni")
    api.speech_to_text_router.side_effect = ConnectionError("down")
    api.speech_to_text.return_value = {"text": "local"}

    assert await handler._transcribe(b"wav") == "local"


@pytest.mark.asyncio
async def test_transcribe_router_no_speech_error_propagates():
    handler, api = _handler("pi-omni")
    api.speech_to_text_router.side_effect = APIError("no speech", 400)

    with pytest.raises(APIError):
        await handler._transcribe(b"wav")
    api.speech_to_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_speak_default_uses_guaardvark_tts():
    handler, api = _handler()
    api.text_to_speech.return_value = {"audio_url": "/api/voice/audio/x.wav"}
    api.fetch_audio_by_url.return_value = b"local-wav"

    await handler.speak("hi")

    api.text_to_speech.assert_awaited_once()
    api.text_to_speech_router.assert_not_awaited()
    handler._play_audio.assert_awaited_once_with(b"local-wav")


@pytest.mark.asyncio
async def test_speak_pi_omni_uses_router():
    handler, api = _handler("pi-omni")
    api.text_to_speech_router.return_value = b"router-wav"

    await handler.speak("hi")

    api.text_to_speech_router.assert_awaited_once_with("hi", voice="af_heart")
    api.text_to_speech.assert_not_awaited()
    handler._play_audio.assert_awaited_once_with(b"router-wav")


@pytest.mark.asyncio
async def test_speak_falls_back_when_router_unreachable():
    handler, api = _handler("pi-omni")
    api.text_to_speech_router.side_effect = ConnectionError("down")
    api.text_to_speech.return_value = {"filename": "x.wav"}
    api.fetch_audio_by_url.return_value = b"local-wav"

    await handler.speak("hi")

    api.text_to_speech.assert_awaited_once()
    handler._play_audio.assert_awaited_once_with(b"local-wav")
