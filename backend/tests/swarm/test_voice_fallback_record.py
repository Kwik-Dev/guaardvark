"""A Film Crew line spoken in a voice other than the one asked for says so.

Two silent paths: Audio Foundry's auto mode falls back from Chatterbox to
Kokoro on any error, and the client drops a Cast voice id that is not a
built-in voice so the default voice speaks. Both now land in a per-line voice
record the Editor returns and run_editor stores on the shot.

The plugin's HTTP reply is stubbed at requests.post, the seam
AudioFoundryClient._generate calls; nothing reaches Audio Foundry.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.swarm import clients
from backend.services.swarm.agents.editor import Editor, ShotInput


class _Reply:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def plugin_reply(monkeypatch, tmp_path):
    """Make the plugin answer with a written file and the given meta."""
    sent = []

    def answer(meta):
        wav = tmp_path / "plugin_out.wav"
        wav.write_bytes(b"RIFF")

        def post(url, json=None, timeout=None):
            sent.append(json)
            return _Reply({"path": str(wav), "meta": meta})

        monkeypatch.setattr(clients.requests, "post", post)
        return sent

    return answer


def test_a_chatterbox_failure_is_recorded_with_the_voice_that_spoke(plugin_reply, tmp_path):
    plugin_reply({"backend": "kokoro", "voice": "af_heart",
                  "fallback": {"from": "chatterbox", "to": "kokoro", "reason": "CUDA out of memory"}})
    client = clients.AudioFoundryClient("http://127.0.0.1:1")
    out = str(tmp_path / "vo.wav")

    assert client.tts(text="Hello there.", voice="default", output_path=out) == out
    record = client.voice_record(out)
    assert record["backend"] == "kokoro" and record["voice"] == "af_heart"
    [fallback] = record["fallbacks"]
    assert fallback["kind"] == "engine_fallback"
    assert fallback["message"] == "Chatterbox failed (CUDA out of memory); Kokoro (af_heart) spoke this line."


def test_a_cast_voice_that_is_not_built_in_is_recorded_not_dropped_quietly(plugin_reply, tmp_path):
    sent = plugin_reply({"backend": "chatterbox"})
    client = clients.AudioFoundryClient("http://127.0.0.1:1")
    out = str(tmp_path / "vo.wav")

    client.tts(text="Hello there.", voice="annas-own-voice", output_path=out)
    assert "voice_id" not in sent[0]
    record = client.voice_record(out)
    assert record["requested_voice"] == "annas-own-voice" and record["voice_id_sent"] is None
    assert record["voice"] == "stock voice"
    assert [f["kind"] for f in record["fallbacks"]] == ["voice_not_built_in"]
    assert "'annas-own-voice' is not one of Audio Foundry's built-in voices" in record["fallbacks"][0]["message"]


def test_a_line_spoken_as_asked_has_no_fallbacks(plugin_reply, tmp_path):
    voice = "bm_george"
    plugin_reply({"backend": "kokoro", "voice": voice})
    client = clients.AudioFoundryClient("http://127.0.0.1:1")
    out = str(tmp_path / "vo.wav")
    client.tts(text="Hello there.", voice=voice, output_path=out)
    assert client.voice_record(out)["fallbacks"] == []


def _shot(num, dialogue):
    return ShotInput(shot_number=num, storyboard_image_path=f"/tmp/s{num}.png", image_prompt="p",
                     duration_seconds=3.0, dialogue_text=dialogue, lora_paths=[], scene_number=1)


def _editor(audio):
    i2v = MagicMock()
    i2v.i2v_from_image.side_effect = lambda **kw: kw["output_path"]
    ffmpeg = MagicMock()
    ffmpeg.concat_with_audio.side_effect = lambda **kw: kw["output_path"]
    return Editor(i2v=i2v, audio_foundry=audio, ffmpeg=ffmpeg)


def test_the_editor_returns_one_voice_record_per_shot(tmp_path):
    record = {"voice": "af_heart", "fallbacks": [{"kind": "engine_fallback", "message": "m"}]}

    class _Audio:
        def tts(self, *, text, voice, output_path):
            return output_path

        def voice_record(self, output_path):
            return record

        def generate_music(self, **kw):
            return None

    result = _editor(_Audio()).render(production_id=1, production_name="P",
                                      shots=[_shot(1, "Hi."), _shot(2, None)], output_dir=str(tmp_path))
    assert result.voice_records == [record, None]


def test_a_line_whose_speech_failed_is_recorded_as_having_no_voiceover(tmp_path):
    class _Audio:
        def tts(self, *, text, voice, output_path):
            raise RuntimeError("audio foundry is down")

        def generate_music(self, **kw):
            return None

    result = _editor(_Audio()).render(production_id=1, production_name="P",
                                      shots=[_shot(1, "Hi.")], output_dir=str(tmp_path))
    [record] = result.voice_records
    assert record["fallbacks"][0]["kind"] == "no_voiceover"
    assert "audio foundry is down" in record["fallbacks"][0]["message"]
    assert result.voiceover_paths == [None]
