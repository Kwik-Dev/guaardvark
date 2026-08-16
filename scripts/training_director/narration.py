"""Narration for training videos.

Synthesizes a trainer's voice from a reference clip via Chatterbox
(audio_foundry `/generate/voice`), verifies every line against the script with
whisper, and assembles takes into one WAV with constructed silence between
lines.

The narrator's clip, delivery preset and trade vocabulary come from the active
project; endpoints come from the environment. Only the audio half of the
walkthrough harness (`scripts/demo_director/`) is carried over — there is no
screen capture here.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

import requests

import context
from config import API, BACKEND_VENV_PY, FOUNDRY, REPO, WHISPER_MODEL
from voice_style import spoken as _spoken

# Whisper similarity below this fails the line. Technical narration transcribes
# imperfectly even when the take is clean, so the bar is deliberately loose —
# it catches repeats, drops and garble, not pronunciation nuance.
MATCH_THRESHOLD = 0.55

# A whole-line ratio hides a mangled opening behind a well-read tail, so the
# first few words are scored separately.
OPENING_WORDS = 4
OPENING_THRESHOLD = 0.45

TAKES_PER_LINE = 3

# Silence prepended before transcription, and before the first line of a shot.
HEAD_PAD_S = 0.3

# Retakes re-roll this so a failed line gets genuinely different sampling while
# every take stays reproducible from its seed.
BASE_SEED = int(os.environ.get("TD_VOICE_SEED", "20260815"))


def spoken(text: str) -> str:
    """Project-aware written-to-spoken rendering."""
    proj = context.current()
    return _spoken(text, proj.terms, proj.spelled_acronyms)


def voice_reference() -> Path:
    ref = context.asset(context.current().voice_reference)
    if ref is None:
        raise RuntimeError(
            "the active project defines no voice_reference; set one in its "
            "project.py, or select a project with TD_PROJECT")
    return ref


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ffprobe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
               "-of", "csv=p=0", str(path)]).stdout.strip()
    return float(out)


def ensure_narrator_ready() -> None:
    """Fail before any synthesis if the voice service or reference clip is missing.

    Shipping a video in the wrong voice is worse than not shipping one, so this
    refuses to continue rather than falling back to a stock voice.
    """
    ref = voice_reference()
    if not ref.exists():
        raise RuntimeError(
            f"voice reference clip not found: {ref}\n"
            "Check voice_reference in the project's project.py.")
    # The stills pass stops audio_foundry to reclaim its CUDA context, so the
    # service being down here is expected rather than an error.
    for attempt in range(2):
        try:
            r = requests.get(f"{FOUNDRY}/health", timeout=8)
            if r.ok:
                return
        except Exception:
            pass
        if attempt == 0:
            print("  narrator: audio_foundry is down — starting it")
            try:
                requests.post(f"{API}/api/plugins/audio_foundry/start",
                              timeout=300)
                time.sleep(3)
            except Exception as e:
                print(f"  narrator: could not start audio_foundry: {e}")
    raise RuntimeError(
        f"audio_foundry is not answering at {FOUNDRY}. Start the plugin, or "
        "point TD_FOUNDRY at a host running it.")


def _synthesize(text: str, dest: Path, seed: int | None = None) -> None:
    """One Chatterbox take of `text` into `dest`."""
    payload = {
        "text": text,
        "backend": "chatterbox",
        "reference_clip_path": str(voice_reference()),
        "emotion": context.current().voice_emotion,
    }
    if seed is not None:
        payload["seed"] = seed
    r = requests.post(f"{FOUNDRY}/generate/voice", json=payload, timeout=600)
    r.raise_for_status()
    res = r.json()
    if "job_id" in res and "path" not in res:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            job = requests.get(f"{FOUNDRY}/jobs/{res['job_id']}", timeout=30).json()
            if job.get("status") in ("done", "completed"):
                res = job.get("result", job)
                break
            if job.get("status") in ("failed", "error"):
                raise RuntimeError(f"voice job failed: {job}")
            time.sleep(2)
        else:
            raise RuntimeError(f"voice job {res['job_id']} never finished")
    dest.write_bytes(Path(res["path"]).read_bytes())


def _letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def transcribe(path: Path) -> str:
    """Transcribe a take. Runs faster-whisper in the backend venv directly —
    the HTTP speech-to-text route rate-limits well below one call per line.

    The take is padded first: Chatterbox starts speaking at sample zero, and
    whisper drops the opening word without a lead-in.
    """
    padded = path.parent / f".{path.stem}_padded.wav"
    run(["ffmpeg", "-y", "-i", str(path), "-af", f"adelay={int(HEAD_PAD_S * 1000)}"
         f"|{int(HEAD_PAD_S * 1000)}", str(padded)])
    code = (
        "import sys\n"
        "from backend.utils.faster_whisper_utils import transcribe_audio_faster\n"
        "print(transcribe_audio_faster(sys.argv[1], model_size=sys.argv[2])[0] or '')\n"
    )
    r = subprocess.run([str(BACKEND_VENV_PY), "-c", code, str(padded),
                        WHISPER_MODEL],
                       capture_output=True, text=True, cwd=str(REPO), timeout=300)
    padded.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"whisper failed: {r.stderr[-400:]}")
    lines = r.stdout.strip().splitlines()
    return lines[-1] if lines else ""


def check_line(expected: str, wav: Path) -> tuple[bool, str]:
    """Whisper-verify a take against the text it was meant to read."""
    heard = transcribe(wav)
    said = spoken(expected)
    want, got = _letters(said), _letters(heard)
    if not want:
        return True, heard
    if len(got) > 1.6 * len(want):        # said too much = repeated itself
        return False, heard
    if difflib.SequenceMatcher(None, want, got).ratio() < MATCH_THRESHOLD:
        return False, heard
    want_open = _letters(" ".join(said.split()[:OPENING_WORDS]))
    got_open = _letters(" ".join(heard.split()[:OPENING_WORDS + 2]))
    if want_open and difflib.SequenceMatcher(
            None, want_open, got_open).ratio() < OPENING_THRESHOLD:
        return False, heard
    return True, heard


def _seed_for(text: str, attempt: int) -> int:
    """Deterministic per-line seed, so a whole guide re-renders identically."""
    digest = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    return (BASE_SEED + digest + attempt * 7919) % (2 ** 31)


def _take(text: str, dest: Path, verify: bool = True) -> None:
    """Synthesize until the take reads clean, keeping the closest on failure.

    Each attempt uses its own seed: retakes differ from one another, and any
    take can be reproduced later from the seed recorded against it.
    """
    if not verify:
        _synthesize(text, dest, seed=_seed_for(text, 0))
        return
    closest: tuple[int, bytes] | None = None
    for attempt in range(1, TAKES_PER_LINE + 1):
        _synthesize(text, dest, seed=_seed_for(text, attempt))
        ok, heard = check_line(text, dest)
        if ok:
            return
        print(f"    read-check failed {attempt}/{TAKES_PER_LINE}\n"
              f"      wanted: {spoken(text)[:70]!r}\n"
              f"      heard : {heard[:70]!r}")
        penalty = abs(len(_letters(heard)) - len(_letters(spoken(text))))
        if closest is None or penalty < closest[0]:
            closest = (penalty, dest.read_bytes())
    if closest is not None:
        dest.write_bytes(closest[1])
        print(f"    kept closest take after {TAKES_PER_LINE} attempts")


def _silence(seconds: float, dest: Path) -> Path:
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{seconds:.2f}", "-sample_fmt", "s16", str(dest)])
    return dest


def generate_narration(lines, dest: Path, line_pause: float = 0.55,
                       verify: bool = True) -> float:
    """Synthesize `lines` into `dest`; returns duration in seconds.

    `lines` is a string or a list of strings. Each line is its own take, joined
    by real silence — pauses are constructed rather than coaxed out of the model.
    An empty string adds another `line_pause` of room before the next line.
    """
    lines = [lines] if isinstance(lines, str) else list(lines)
    workdir = dest.parent / f".{dest.stem}_takes"
    workdir.mkdir(parents=True, exist_ok=True)

    # Takes begin at sample zero; without this the shot opens on a hard cut.
    parts: list[Path] = [_silence(HEAD_PAD_S, workdir / "sil_head.wav")]
    spoken_lines = 0
    pending_pause = 0.0
    for i, line in enumerate(lines):
        if not line.strip():
            pending_pause += line_pause
            continue
        raw = workdir / f"raw_{i:02d}.wav"
        _take(spoken(line), raw, verify=verify)
        # Backends differ in sample rate; unify before concat.
        norm = workdir / f"seg_{i:02d}.wav"
        run(["ffmpeg", "-y", "-i", str(raw), "-ar", "24000", "-ac", "1",
             "-sample_fmt", "s16", str(norm)])
        if spoken_lines:
            parts.append(_silence(line_pause + pending_pause,
                                  workdir / f"sil_{i:02d}.wav"))
        elif pending_pause:
            parts.append(_silence(pending_pause, workdir / f"sil_{i:02d}.wav"))
        pending_pause = 0.0
        parts.append(norm)
        spoken_lines += 1

    if not spoken_lines:
        raise RuntimeError("narration had no speakable lines")
    concat = workdir / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-c", "copy", str(dest)])
    duration = ffprobe_duration(dest)
    if duration <= 0.2:
        raise RuntimeError(f"narration suspiciously short ({duration}s)")
    return duration
