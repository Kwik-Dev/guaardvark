"""Song structure analysis: beats, bars, loudness and sections.

Two section modes:

- ``section_count=N`` divides the song into N equal-time slices and labels them
  by onset energy ('intro', 'build', 'drop', 'outro'). A placement bias for the
  beat-sync arranger, not a reading of the song.
- ``section_count=None`` reads the song: a bar grid on the beats (4/4 assumed,
  downbeat phase estimated from low-band onsets and harmonic change), loudness
  per bar normalised within the song to 0..1, and sections where the harmony
  and timbre change (bar-synced chroma + MFCC, agglomerative clustering),
  labelled by position and loudness. The music-video planner uses this mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SECTION_COUNT = 4
BEATS_PER_BAR = 4          # the bar grid assumes 4/4
_FEATURE_SR = 22050
_HOP = 512
_MIN_SECTION_BARS = 4      # shorter detected sections merge into a neighbour
_SIMILAR_ENERGY = 0.15     # neighbouring sections this close in loudness merge
_LOUD = 0.7                # section loudness (0..1) labelled a drop
_QUIET = 0.3               # middle-section loudness labelled a breakdown


@dataclass
class SongSection:
    label: str               # intro | verse | build | drop | breakdown | outro | unlabeled
    start: float             # seconds
    end: float
    mean_energy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "mean_energy": self.mean_energy,
        }


@dataclass
class SongStructure:
    tempo_bpm: float
    duration_seconds: float
    beat_times: list[float] = field(default_factory=list)
    sections: list[SongSection] = field(default_factory=list)
    # Read-the-song mode only (section_count=None); empty otherwise.
    downbeat_times: list[float] = field(default_factory=list)   # bar starts
    bar_energy: list[float] = field(default_factory=list)       # 0..1 loudness, one per bar
    beats_per_bar: int = BEATS_PER_BAR

    def to_dict(self) -> dict[str, Any]:
        return {
            "tempo_bpm": self.tempo_bpm,
            "duration_seconds": self.duration_seconds,
            "beat_times": self.beat_times,
            "sections": [s.to_dict() for s in self.sections],
            "downbeat_times": self.downbeat_times,
            "bar_energy": self.bar_energy,
            "beats_per_bar": self.beats_per_bar,
        }


def analyze_song(
    audio_path: str | Path,
    *,
    section_count: int | None = DEFAULT_SECTION_COUNT,
    tightness: int = 100,
) -> SongStructure:
    """Extract tempo, beats, and labeled sections from an audio file.

    ``section_count=None`` also returns the bar grid and per-bar loudness and
    finds the sections from the music (see the module docstring).
    """
    import librosa
    import numpy as np

    path = Path(audio_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"audio not found: {path}")

    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = float(len(y)) / float(sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        tightness=tightness,
        trim=True,
        units="frames",
    )
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    if hasattr(tempo, "__len__"):
        tempo = float(np.asarray(tempo).reshape(-1)[0])
    else:
        tempo = float(tempo)

    if section_count is None:
        grid = _read_structure(y, sr, duration, beat_times)
        if grid is not None:
            downbeats, bar_energy, sections = grid
            return SongStructure(
                tempo_bpm=tempo,
                duration_seconds=duration,
                beat_times=beat_times,
                sections=sections,
                downbeat_times=downbeats,
                bar_energy=bar_energy,
            )
        section_count = DEFAULT_SECTION_COUNT   # too few beats for a bar grid

    sections = _segment_by_energy(onset_env, sr, duration, section_count)

    return SongStructure(
        tempo_bpm=tempo,
        duration_seconds=duration,
        beat_times=beat_times,
        sections=sections,
    )


def _read_structure(y, sr: int, duration: float, beat_times: list[float]):
    """(downbeat times, per-bar loudness 0..1, sections), or None without enough beats."""
    import librosa
    import numpy as np

    beats = np.asarray(beat_times, dtype=float)
    if len(beats) < 4 * BEATS_PER_BAR:
        return None
    yf = librosa.resample(y, orig_sr=sr, target_sr=_FEATURE_SR) if sr != _FEATURE_SR else y
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(y=yf, hop_length=_HOP)[0], ref=np.max)
    n_frames = len(rms_db)
    beat_frames = np.clip(librosa.time_to_frames(beats, sr=_FEATURE_SR, hop_length=_HOP), 0, n_frames - 1)

    def per_beat(feature, aggregate):
        # sync pads a segment before the first beat; keep one column per beat
        return librosa.util.sync(feature, beat_frames, aggregate=aggregate)[:, 1:len(beats) + 1]

    beat_db = per_beat(rms_db[np.newaxis, :], np.mean)[0]
    chroma = librosa.feature.chroma_cqt(y=yf, sr=_FEATURE_SR, hop_length=_HOP)

    # Downbeat phase: bar starts carry low-band onsets (kick, bass) and harmonic changes.
    low_onset = librosa.onset.onset_strength(y=yf, sr=_FEATURE_SR, hop_length=_HOP, fmax=200, n_mels=32)
    low_at_beat = low_onset[np.clip(beat_frames, 0, len(low_onset) - 1)]
    beat_chroma = per_beat(chroma, np.median)
    change = np.r_[0.0, np.linalg.norm(np.diff(beat_chroma, axis=1), axis=0)]

    def z(v):
        return (v - v.mean()) / (v.std() + 1e-9)

    score = z(low_at_beat) + z(change)
    phase = int(np.argmax([score[p::BEATS_PER_BAR].mean() for p in range(BEATS_PER_BAR)]))
    bar_idx = list(range(phase, len(beats), BEATS_PER_BAR))
    bars = beats[bar_idx]

    # Loudness per bar, normalised within the song (10th..95th percentile -> 0..1).
    bar_db = np.array([beat_db[i:i + BEATS_PER_BAR].mean() for i in bar_idx])
    lo, hi = np.percentile(bar_db, 10), np.percentile(bar_db, 95)
    if hi - lo < 1e-6:
        bar_energy = np.full(len(bars), 0.5)
    else:
        bar_energy = np.clip((bar_db - lo) / (hi - lo), 0.0, 1.0)

    # Sections: where harmony and timbre change, on bar boundaries.
    mfcc = librosa.feature.mfcc(y=yf, sr=_FEATURE_SR, hop_length=_HOP, n_mfcc=13)
    bar_frames = np.clip(librosa.time_to_frames(bars, sr=_FEATURE_SR, hop_length=_HOP), 0, n_frames - 1)
    feats = np.vstack([librosa.util.sync(chroma, bar_frames), librosa.util.sync(mfcc, bar_frames)])
    feats = feats[:, 1:len(bars) + 1]
    feats = (feats - feats.mean(axis=1, keepdims=True)) / (feats.std(axis=1, keepdims=True) + 1e-9)
    k = int(np.clip(round(duration / 24), 4, 10))
    k = max(1, min(k, len(bars)))
    starts = [int(b) for b in librosa.segment.agglomerative(feats, k)]
    spans = _merge_spans(starts, bar_energy, len(bars))

    sections = []
    for s, e in spans:
        start = 0.0 if s == 0 else float(bars[s])
        end = float(bars[e]) if e < len(bars) else duration
        sections.append(SongSection(label="unlabeled", start=start, end=end,
                                    mean_energy=float(bar_energy[s:e].mean())))
    _label_by_loudness(sections)
    return [float(b) for b in bars], [float(v) for v in bar_energy], sections


def _merge_spans(starts: list[int], bar_energy, n_bars: int) -> list[list[int]]:
    """[start_bar, end_bar) spans from segment starts, with short or same-loudness
    neighbours merged so the sections are ones a listener would name."""
    cuts = sorted({0, *[s for s in starts if 0 < s < n_bars]})
    spans = [[s, e] for s, e in zip(cuts, cuts[1:] + [n_bars])]
    merged = True
    while merged and len(spans) > 1:
        merged = False
        for i, (s, e) in enumerate(spans):
            nxt = spans[i + 1] if i + 1 < len(spans) else None
            similar = nxt is not None and abs(
                bar_energy[s:e].mean() - bar_energy[nxt[0]:nxt[1]].mean()) < _SIMILAR_ENERGY
            if e - s < _MIN_SECTION_BARS or similar:
                if nxt is not None:
                    spans[i] = [s, nxt[1]]
                    del spans[i + 1]
                else:
                    spans[i - 1][1] = e
                    del spans[i]
                merged = True
                break
    return spans


def _label_by_loudness(sections: list[SongSection]) -> None:
    """Quiet first and last sections are the intro and outro; the rest are named by
    loudness: drop (loud), breakdown (quiet), build (rising into a drop), verse."""
    def by_energy(i: int) -> str:
        e = sections[i].mean_energy
        if e >= _LOUD:
            return "drop"
        if e <= _QUIET and 0 < i < len(sections) - 1:
            return "breakdown"
        nxt = sections[i + 1] if i + 1 < len(sections) else None
        if nxt is not None and nxt.mean_energy >= _LOUD:
            return "build"
        return "verse"

    for i, sec in enumerate(sections):
        sec.label = by_energy(i)
    if len(sections) > 1:
        if sections[0].mean_energy < 0.5:
            sections[0].label = "intro"
        if sections[-1].mean_energy < 0.5:
            sections[-1].label = "outro"


def _segment_by_energy(
    onset_env, sr: int, duration: float, n: int
) -> list[SongSection]:
    """Split the song into N equal-time slices, label by their mean energy.

    Labels are assigned based on the rank-position of each section's energy:
    lowest-energy slices become intro/outro depending on position, highest
    become drop, middling become build. v1 heuristic — good enough as a bias
    signal for the arranger, not a music-theory claim.
    """
    import librosa
    import numpy as np

    if n < 2:
        sections = [SongSection(label="unlabeled", start=0.0, end=duration, mean_energy=float(np.mean(onset_env)))]
        _label_sections(sections)
        return sections

    env_times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr)
    slice_dur = duration / n
    sections: list[SongSection] = []
    for i in range(n):
        start = i * slice_dur
        end = duration if i == n - 1 else (i + 1) * slice_dur
        mask = (env_times >= start) & (env_times < end)
        mean_energy = float(np.mean(onset_env[mask])) if mask.any() else 0.0
        sections.append(SongSection(label="unlabeled", start=start, end=end, mean_energy=mean_energy))

    _label_sections(sections)
    return sections


def _label_sections(sections: list[SongSection]) -> None:
    """Assign intro/build/drop/outro based on position + energy ranking."""
    if not sections:
        return
    if len(sections) == 1:
        sections[0].label = "drop"
        return

    n = len(sections)
    # Position-first heuristic: the first is intro, the last is outro.
    sections[0].label = "intro"
    sections[-1].label = "outro"

    # Middle sections: the highest-energy is the drop, others are build.
    middles = sections[1:-1]
    if not middles:
        return
    max_energy = max(s.mean_energy for s in middles)
    for s in middles:
        s.label = "drop" if s.mean_energy >= max_energy else "build"
