"""FfmpegRunner's film mix: each stem levelled, summed without scaling, the
whole film brought to the speech target under the limiter."""
from backend.services.swarm import clients
from backend.services.swarm.clients import (
    FILM_MUSIC_LUFS, FILM_PEAK_LIMIT, FILM_SPEECH_COMPRESSOR, FILM_SPEECH_LUFS,
    FfmpegRunner, _gain_db,
)


def test_gain_brings_a_stem_to_its_target():
    assert _gain_db(-19.0, FILM_SPEECH_LUFS) == 3.0
    assert _gain_db(-14.0, FILM_MUSIC_LUFS) == -11.0


def test_gain_leaves_silent_or_unmeasured_stems_alone():
    assert _gain_db(None, FILM_SPEECH_LUFS) == 0.0
    assert _gain_db(-70.0, FILM_SPEECH_LUFS) == 0.0


def test_gain_is_clamped():
    assert _gain_db(-55.0, FILM_SPEECH_LUFS) == clients._MAX_GAIN_DB


def _runner(monkeypatch, loudness):
    runner = FfmpegRunner()
    calls = []
    monkeypatch.setattr(runner, "_run", lambda args: calls.append(args))
    monkeypatch.setattr(runner, "probe_duration", lambda path: 3.0)
    measured = []

    def measure(path, pre_filter=None):
        measured.append((path, pre_filter))
        return loudness(path)

    monkeypatch.setattr(runner, "measure_loudness", measure)
    return runner, calls, measured


def test_mix_levels_each_stem_and_sums_them(monkeypatch, tmp_path):
    levels = {"vo1.wav": -19.0, "score.wav": -14.0, "_mixed_raw.wav": -17.0}
    runner, calls, measured = _runner(
        monkeypatch, lambda path: next(v for k, v in levels.items() if path.endswith(k)))

    runner.concat_with_audio(
        video_clips=["a.mp4", "b.mp4"], voiceovers=["vo1.wav", None],
        music_track="score.wav", output_path=str(tmp_path / "final.mp4"),
    )

    concat, mix, master, mux = calls
    graph = mix[mix.index("-filter_complex") + 1]
    assert f"[0:a]{FILM_SPEECH_COMPRESSOR},volume=3.00dB,adelay=0|0[a0]" in graph
    assert "[1:a]volume=-11.00dB,atrim=0:6.000[a1]" in graph
    assert "normalize=0" in graph
    assert "apad=whole_dur=6.000,atrim=0:6.000[aout]" in graph
    assert ("vo1.wav", FILM_SPEECH_COMPRESSOR) in measured

    af = master[master.index("-af") + 1]
    assert af == f"volume=1.00dB,alimiter=limit={FILM_PEAK_LIMIT}:level=0:latency=1"
    assert mux[-1].endswith("final.mp4")


def test_mix_keeps_an_unmeasurable_stem_at_its_own_level(monkeypatch, tmp_path):
    runner, calls, _ = _runner(monkeypatch, lambda path: None)

    runner.concat_with_audio(
        video_clips=["a.mp4"], voiceovers=["vo1.wav"],
        music_track=None, output_path=str(tmp_path / "final.mp4"),
    )

    _, mix, master, _ = calls
    graph = mix[mix.index("-filter_complex") + 1]
    assert "volume=0.00dB" in graph
    assert master[master.index("-af") + 1].startswith("volume=0.00dB,")
