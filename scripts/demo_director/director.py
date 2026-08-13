"""demo_director — deterministic walkthrough-video harness.

Records the Guaardvark UI on a dedicated 1920x1080 Xvfb display, driven by
Playwright for element coordinates and xdotool for real, visible cursor motion.
Narration is generated FIRST (Piper via /api/voice/narrate); each beat's screen
time is held until its narration finishes, so video >= audio per beat by
construction. One recording per beat; failed beats retake automatically.

Design notes (why this exists instead of scripts/agent_demo.py):
  - capture size derived from xdpyinfo, ffmpeg poll()-checked (agent_demo's
    1024x1024-on-1000x1000 capture failed silently on every run)
  - per-beat mux with apad instead of one -shortest mux over concatenated
    narration (which discarded ~70% of footage and drifted out of sync)
  - Playwright supplies coordinates; clicks go through xdotool so the recorded
    X cursor actually moves (Playwright-native clicks are synthetic: no cursor)
  - zero VRAM: no vision model anywhere on the critical path
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

API = os.environ.get("GUAARDVARK_API", "http://localhost:5000")
FRONTEND = os.environ.get("GUAARDVARK_FRONTEND", "http://localhost:5173")
DISPLAY = os.environ.get("DEMO_DISPLAY", ":98")
FPS = 30
CURSOR_SIZE = os.environ.get("DEMO_CURSOR_SIZE", "48")


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def display_size(display: str = DISPLAY) -> tuple[int, int]:
    out = _run(["xdpyinfo"], env={**os.environ, "DISPLAY": display}).stdout
    for line in out.splitlines():
        if "dimensions:" in line:
            dims = line.split()[1]
            w, h = dims.split("x")
            return int(w), int(h)
    raise RuntimeError(f"no dimensions from xdpyinfo on {display}")


# ---------------------------------------------------------------- narration

# Spoken-text substitutions applied to narration ONLY (on-screen text keeps
# the real spelling). Piper and Chatterbox both read "Guaardvark" as
# "gwaaardvark"; the respelling lands the intended "GARD-vark".
PRONUNCIATIONS = {
    "Guaardvark": "Guard-vark",
    "guaardvark": "Guard-vark",
}

# Series narrator: Chatterbox clone of the Piper female voice (more dynamic
# prosody, same identity). Reference clip lives in the consent-gated store.
NARRATOR_ENGINE = os.environ.get("DEMO_NARRATOR", "chatterbox")
NARRATOR_REF = os.environ.get(
    "DEMO_NARRATOR_REF",
    "data/uploads/voice_references/piper-female-series-narrator.wav")


def speakable(text: str) -> str:
    for word, spoken in PRONUNCIATIONS.items():
        text = text.replace(word, spoken)
    return text


def ffprobe_duration(path: Path) -> float:
    out = _run([
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ]).stdout.strip()
    return float(out)


def _narrate_piper(text: str, dest: Path, voice: str = "libritts") -> None:
    r = requests.post(
        f"{API}/api/voice/narrate",
        json={"script": text, "voice": voice, "output_format": "wav"},
        timeout=120,
    )
    r.raise_for_status()
    info = r.json()
    audio = requests.get(f"{API}/api{info['audio_url']}", timeout=60)
    if audio.status_code == 404:  # some builds serve without /api prefix
        audio = requests.get(f"{API}{info['audio_url']}", timeout=60)
    audio.raise_for_status()
    dest.write_bytes(audio.content)


def _narrate_chatterbox(text: str, dest: Path, seed: int | None = None) -> None:
    ref = Path(NARRATOR_REF)
    if not ref.is_absolute():
        ref = Path(__file__).resolve().parents[2] / ref
    payload = {"text": text, "backend": "chatterbox",
               "reference_clip_path": str(ref)}
    if seed is not None:
        payload["seed"] = seed
    r = requests.post(
        f"{API}/api/audio-foundry/generate/voice",
        json=payload,
        timeout=600,
    )
    r.raise_for_status()
    res = r.json()
    if "job_id" in res and "path" not in res:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            j = requests.get(f"{API}/api/audio-foundry/jobs/{res['job_id']}",
                             timeout=30).json()
            if j.get("status") in ("done", "completed"):
                res = j.get("result", j)
                break
            if j.get("status") in ("failed", "error"):
                raise RuntimeError(f"chatterbox job failed: {j}")
            time.sleep(2)
    src = Path(res["path"])  # service runs on this machine
    dest.write_bytes(src.read_bytes())


def _letters(s: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", s.lower())


def _stt(path: Path) -> str:
    """Transcribe via the backend venv's faster-whisper directly — the HTTP
    /speech-to-text route rate-limits after a handful of calls (429s observed
    mid-episode), and narration prep makes one call per line."""
    repo = Path(__file__).resolve().parents[2]
    py = repo / "backend" / "venv" / "bin" / "python"
    code = (
        "import sys\n"
        "from backend.utils.faster_whisper_utils import transcribe_audio_faster\n"
        "print(transcribe_audio_faster(sys.argv[1], model_size='tiny.en')[0] or '')\n"
    )
    r = subprocess.run([str(py), "-c", code, str(path)],
                       capture_output=True, text=True, cwd=str(repo),
                       timeout=120)
    if r.returncode == 0:
        return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    # fallback: the HTTP route (may rate-limit, but better than nothing)
    with open(path, "rb") as f:
        resp = requests.post(f"{API}/api/voice/speech-to-text",
                             files={"audio": (path.name, f, "audio/wav")},
                             timeout=120)
    resp.raise_for_status()
    return resp.json().get("text", "") or ""


def _line_matches(expected: str, wav: Path) -> tuple[bool, str]:
    """Whisper-verify a synthesized line: catches Chatterbox babble-repeats
    (observed: 'This is Guaardvark' spoken twice), drops, and garble."""
    import difflib
    heard = _stt(wav)
    e, h = _letters(speakable(expected)), _letters(heard)
    if not e:
        return True, heard
    if len(h) > 1.6 * len(e):                      # said too much = repeated
        return False, heard
    sim = difflib.SequenceMatcher(None, e, h).ratio()
    return sim >= 0.55, heard


def _synth_one(text: str, dest: Path, voice: str) -> None:
    if NARRATOR_ENGINE != "chatterbox":
        _narrate_piper(text, dest, voice)
        return
    # chatterbox occasionally repeats/garbles short lines — verify each take
    # against the script via whisper and re-roll the seed until it reads clean
    best: tuple[float, bytes] | None = None
    for attempt in range(3):
        try:
            _narrate_chatterbox(text, dest,
                                seed=None if attempt == 0 else attempt * 7919)
        except Exception as e:
            print(f"  narrator: chatterbox failed ({e}) — falling back to piper")
            _narrate_piper(text, dest, voice)
            return
        try:
            ok, heard = _line_matches(text, dest)
        except Exception as e:
            print(f"  narrator: STT check unavailable ({e}) — accepting take")
            return
        if ok:
            return
        print(f"  narrator: line failed read-check (attempt {attempt + 1}) — "
              f"expected {text[:40]!r}, heard {heard[:60]!r}")
        size_penalty = abs(len(_letters(heard)) - len(_letters(speakable(text))))
        if best is None or size_penalty < best[0]:
            best = (size_penalty, dest.read_bytes())
    if best is not None:                     # all takes flawed — keep closest
        dest.write_bytes(best[1])
        print("  narrator: kept closest take after 3 attempts")


def generate_narration(text, dest: Path, voice: str = "libritts",
                       line_pause: float = 0.55) -> float:
    """Synthesize narration to dest. Returns duration in seconds.

    `text` may be a single string, or a LIST of lines: each line is
    synthesized as its own take (consistent prosody, no TTS chunk seams) and
    the lines are joined with `line_pause` seconds of real silence — pauses
    are constructed, not hoped for. An empty-string line doubles the pause.
    Engine per DEMO_NARRATOR: 'chatterbox' (series default — cloned female
    narrator) with automatic Piper fallback, or 'piper'.
    """
    lines = [text] if isinstance(text, str) else list(text)
    workdir = dest.parent / f".{dest.stem}_parts"
    workdir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []          # normalized 24k mono segments, in order
    pending_pause = 0.0

    def _silence(seconds: float, idx: int) -> Path:
        p = workdir / f"sil_{idx:02d}.wav"
        _run(["ffmpeg", "-y", "-f", "lavfi",
              "-i", "anullsrc=r=24000:cl=mono",
              "-t", f"{seconds:.2f}", "-sample_fmt", "s16", str(p)])
        return p

    for i, line in enumerate(lines):
        if not line.strip():                 # blank line = extra breathing room
            pending_pause += line_pause
            continue
        raw = workdir / f"raw_{i:02d}.wav"
        _synth_one(speakable(line), raw, voice)
        norm = workdir / f"seg_{i:02d}.wav"  # engines differ in rate — unify
        _run(["ffmpeg", "-y", "-i", str(raw), "-ar", "24000", "-ac", "1",
              "-sample_fmt", "s16", str(norm)])
        if parts:
            parts.append(_silence(line_pause + pending_pause, i))
        pending_pause = 0.0
        parts.append(norm)

    if not parts:
        raise RuntimeError("narration had no speakable lines")
    concat = workdir / "concat.txt"
    concat.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
          "-c", "copy", str(dest)])
    dur = ffprobe_duration(dest)
    if dur <= 0.2:
        raise RuntimeError(f"narration suspiciously short ({dur}s)")
    return dur


# ---------------------------------------------------------------- recorder

class Recorder:
    """Per-beat ffmpeg x11grab recorder. Start is verified; stop finalizes."""

    def __init__(self, out_path: Path, display: str = DISPLAY):
        self.out_path = out_path
        self.display = display
        self.proc: subprocess.Popen | None = None
        self.t0 = 0.0

    def start(self):
        w, h = display_size(self.display)
        cmd = [
            "ffmpeg", "-y", "-f", "x11grab", "-draw_mouse", "1",
            "-framerate", str(FPS), "-video_size", f"{w}x{h}",
            "-i", self.display,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            str(self.out_path),
        ]
        # stderr to a FILE, never a pipe: ffmpeg logs stats continuously and a
        # full unread pipe buffer would stall the capture mid-take
        self._errlog = open(str(self.out_path) + ".ffmpeg.log", "w")
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=self._errlog, text=True
        )
        time.sleep(0.6)
        if self.proc.poll() is not None:  # died immediately — surface stderr
            self._errlog.close()
            err = Path(str(self.out_path) + ".ffmpeg.log").read_text()
            raise RuntimeError(f"ffmpeg failed to start: {err[-800:]}")
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def stop(self) -> float:
        assert self.proc is not None
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if not self.out_path.exists() or self.out_path.stat().st_size < 10_000:
            raise RuntimeError(f"recording missing/empty: {self.out_path}")
        return ffprobe_duration(self.out_path)


# ---------------------------------------------------------------- cursor

class Cursor:
    """Real X cursor via xdotool — visible in the recording."""

    def __init__(self, display: str = DISPLAY):
        self.env = {**os.environ, "DISPLAY": display}
        self.pos = (960, 540)

    def _xdo(self, *args):
        subprocess.run(["xdotool", *args], env=self.env, check=True,
                       capture_output=True)

    def jump(self, x: int, y: int):
        self._xdo("mousemove", str(int(x)), str(int(y)))
        self.pos = (int(x), int(y))

    def glide(self, x: int, y: int, dur: float = 0.7, steps: int = 28):
        x0, y0 = self.pos
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)  # smoothstep: ease in/out
            self._xdo("mousemove",
                      str(int(x0 + (x - x0) * e)), str(int(y0 + (y - y0) * e)))
            time.sleep(dur / steps)
        self.pos = (int(x), int(y))

    def click(self, button: int = 1):
        self._xdo("click", str(button))

    def double_click(self):
        self._xdo("click", "--repeat", "2", "--delay", "80", "1")

    def drag(self, x: int, y: int, dur: float = 1.0):
        self._xdo("mousedown", "1")
        time.sleep(0.15)
        self.glide(x, y, dur=dur)
        time.sleep(0.15)
        self._xdo("mouseup", "1")

    def type_text(self, text: str, delay_ms: int = 45):
        self._xdo("type", "--delay", str(delay_ms), text)


# ---------------------------------------------------------------- stage

class Stage:
    """Playwright-headed Chromium on the recording display + cursor helpers."""

    def __init__(self, display: str = DISPLAY):
        from playwright.sync_api import sync_playwright
        self.display = display
        # DISPLAY must be set at the driver level: the browser is spawned by
        # playwright's node driver, and launch(env=...) does not reliably
        # reach the main chrome process (observed: window opened on :0).
        os.environ["DISPLAY"] = display
        # big, high-visibility cursor for the camera. XCURSOR_SIZE alone is
        # ignored on bare Xvfb — an explicit theme must be named too.
        os.environ["XCURSOR_SIZE"] = CURSOR_SIZE
        os.environ["XCURSOR_THEME"] = os.environ.get("DEMO_CURSOR_THEME",
                                                     "DMZ-White")
        # Host session is Wayland: chromium's ozone would auto-pick wayland and
        # open on the REAL desktop, ignoring DISPLAY. Force X11 and scrub the
        # wayland handles so the window can only land on the Xvfb display.
        os.environ.pop("WAYLAND_DISPLAY", None)
        os.environ["XDG_SESSION_TYPE"] = "x11"
        # Bare Xvfb has no WM: kiosk/fullscreen falls back to a 1280x800
        # floating window and nothing manages focus. Openbox (already a
        # Guaardvark agent-display dependency) fixes both. Safe to attempt
        # when one is already running — the second instance just exits.
        subprocess.Popen(["openbox"], env={**os.environ, "DISPLAY": display},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        self._pw = sync_playwright().start()
        w, h = display_size(display)
        # persistent context: --kiosk only applies to the browser's INITIAL
        # window, and launch()+new_page() would open a second, non-kiosk
        # window (observed: tab bar + URL bar on camera). The persistent
        # context's first page IS the kiosk window.
        import tempfile
        self._profile_dir = tempfile.mkdtemp(prefix="demo_stage_chrome_")
        self.browser = self._pw.chromium.launch_persistent_context(
            user_data_dir=self._profile_dir,
            headless=False,
            no_viewport=True,
            args=[
                "--ozone-platform=x11",
                "--kiosk", f"--window-position=0,0", f"--window-size={w},{h}",
                "--hide-crash-restore-bubble", "--disable-infobars",
            ],
        )
        self.page = (self.browser.pages[0] if self.browser.pages
                     else self.browser.new_page())
        self.cursor = Cursor(display)
        time.sleep(1.0)
        self._assert_on_display()
        self._ensure_fullscreen()

    def _ensure_fullscreen(self):
        """--kiosk under openbox still leaves browser chrome (tab + URL bar)
        visible; F11 is what actually fullscreens the content. Verify by
        measuring the viewport against the display and retry once."""
        w, h = display_size(self.display)
        for _ in range(3):
            size = self.page.evaluate(
                "() => [window.innerWidth, window.innerHeight]")
            if size[0] >= w - 4 and size[1] >= h - 4:
                return
            self.cursor.jump(w // 2, h // 2)
            self.cursor.click()          # window must be focused for F11
            time.sleep(0.3)
            self.cursor._xdo("key", "--clearmodifiers", "F11")
            time.sleep(1.2)
        size = self.page.evaluate("() => [window.innerWidth, window.innerHeight]")
        raise RuntimeError(f"could not fullscreen the stage: viewport={size}, "
                           f"display={w}x{h}")

    def _assert_on_display(self):
        """Hard-fail unless a window is actually mapped on the recording display."""
        out = subprocess.run(
            ["xwininfo", "-root", "-tree"],
            env={**os.environ, "DISPLAY": self.display},
            capture_output=True, text=True,
        ).stdout
        for line in out.splitlines():
            if "children" in line and line.strip().startswith("0 children"):
                raise RuntimeError(
                    f"browser did not open on {self.display} — root has no "
                    "child windows; refusing to record a black screen")

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    # -- coordinate mapping (kiosk => ~identity, but computed, not assumed)
    def _offsets(self) -> tuple[int, int]:
        m = self.page.evaluate(
            "() => [window.screenX + (window.outerWidth - window.innerWidth),"
            " window.screenY + (window.outerHeight - window.innerHeight)]"
        )
        return int(m[0]), int(m[1])

    def screen_xy(self, locator) -> tuple[int, int]:
        locator.first.wait_for(state="visible", timeout=10_000)
        # off-screen elements (e.g. sidebar items below the fold) would clamp
        # the cursor at the display edge — scroll them into view first
        locator.first.scroll_into_view_if_needed(timeout=5_000)
        time.sleep(0.3)
        box = locator.first.bounding_box()
        if not box:
            raise RuntimeError("element has no bounding box")
        ox, oy = self._offsets()
        return int(box["x"] + box["width"] / 2 + ox), int(box["y"] + box["height"] / 2 + oy)

    # -- camera-visible actions
    def glide_click(self, locator, dur: float = 0.7, double: bool = False):
        x, y = self.screen_xy(locator)
        self.cursor.glide(x, y, dur=dur)
        time.sleep(0.25)
        (self.cursor.double_click if double else self.cursor.click)()

    def path(self) -> str:
        """Live SPA path straight from the browser. NEVER poll page.url for
        SPA navigation in sync Playwright: it's a locally cached property that
        only refreshes when other RPCs pump the event loop, so a pure
        sleep/read poll can sit on a stale value forever while the real
        browser has long since navigated (observed exactly that)."""
        return self.page.evaluate("() => location.pathname")

    def nav_via_sidebar(self, label: str, expect_path: str, expect_locator=None):
        self.glide_click(self.page.locator(f"a[aria-label='{label}']"))
        # poll the LIVE path; visible re-clicks before giving up
        for attempt in range(3):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if expect_path in self.path():
                    if expect_locator is not None:
                        expect_locator.first.wait_for(state="visible",
                                                      timeout=15_000)
                    return
                time.sleep(0.2)
            if attempt < 2:
                self.cursor.click()
        snap = f"/tmp/demo_nav_fail_{int(time.time())}.png"
        subprocess.run(["import", "-window", "root", "-display", self.display,
                        snap], check=False)
        raise RuntimeError(
            f"sidebar nav to {expect_path} failed; path={self.path()}; "
            f"screen: {snap}")

    def hover_over(self, locator, dur: float = 0.7):
        x, y = self.screen_xy(locator)
        self.cursor.glide(x, y, dur=dur)


# ---------------------------------------------------------------- beats

@dataclass
class Beat:
    name: str
    narration: str
    action: "callable"          # fn(stage) -> None; raises to fail the take
    verify: "callable" = None   # fn(stage) -> None; raises to fail the take
    reset: "callable" = None    # fn(stage) -> None; runs BEFORE each take's
                                # recording starts — must restore a clean,
                                # identical starting state (retakes depend on it)
    min_hold: float = 2.0       # extra floor beyond narration
    lead_in: float = 0.8        # settle time recorded before actions start
    retakes: int = 3
    # Demo audio mixed into the beat at mux time: [(wav_path, start_s), ...].
    # x11grab records VIDEO ONLY — anything the UI "plays" is silent unless
    # it is scheduled here (essential for the audio episodes).
    audio_overlays: list = field(default_factory=list)
    audio_path: Path = field(default=None, repr=False)
    audio_dur: float = 0.0


class Episode:
    def __init__(self, slug: str, beats: list[Beat], out_root: Path | None = None):
        self.slug = slug
        self.beats = beats
        ts = time.strftime("%Y%m%d_%H%M%S")
        root = out_root or Path("data/outputs/demos")
        self.dir = root / f"{slug}_{ts}"
        self.dir.mkdir(parents=True, exist_ok=True)

    # narration first — sync by construction
    def prepare_audio(self):
        for i, b in enumerate(self.beats):
            b.audio_path = self.dir / f"beat_{i:02d}_{b.name}.wav"
            b.audio_dur = generate_narration(b.narration, b.audio_path)
            print(f"  audio {b.name}: {b.audio_dur:.1f}s")

    def _record_beat(self, stage: Stage, i: int, b: Beat) -> Path:
        raw = self.dir / f"beat_{i:02d}_{b.name}.raw.mp4"
        for attempt in range(1, b.retakes + 1):
            raw.unlink(missing_ok=True)
            rec = Recorder(raw)
            try:
                if b.reset:
                    b.reset(stage)
                rec.start()
                time.sleep(b.lead_in)
                b.action(stage)
                if b.verify:
                    b.verify(stage)
                target = max(b.audio_dur + 0.7, b.min_hold)
                while rec.elapsed() < target:
                    time.sleep(0.1)
                vdur = rec.stop()
                if vdur + 0.3 < b.audio_dur:
                    raise RuntimeError(
                        f"video {vdur:.1f}s shorter than narration {b.audio_dur:.1f}s")
                print(f"  take ok {b.name}: video {vdur:.1f}s / audio {b.audio_dur:.1f}s")
                return raw
            except Exception as e:
                try:
                    if rec.proc and rec.proc.poll() is None:
                        rec.stop()
                except Exception:
                    pass
                snap = self.dir / f"fail_{b.name}_take{attempt}.png"
                subprocess.run(["import", "-window", "root", "-display",
                                DISPLAY, str(snap)], check=False)
                print(f"  RETAKE {b.name} (attempt {attempt}/{b.retakes}): {e}")
                if attempt == b.retakes:
                    raise
                time.sleep(1.5)

    def _mux_beat(self, i: int, b: Beat, raw: Path) -> Path:
        out = self.dir / f"beat_{i:02d}_{b.name}.mp4"
        if not b.audio_overlays:
            _run([
                "ffmpeg", "-y", "-i", str(raw), "-i", str(b.audio_path),
                "-filter_complex", "[1:a]apad[a]",
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", str(out),
            ])
            return out
        # narration + scheduled demo audio (adelay to its start time, amix)
        cmd = ["ffmpeg", "-y", "-i", str(raw), "-i", str(b.audio_path)]
        filters = ["[1:a]apad[nar]"]
        mix_inputs = "[nar]"
        for k, (opath, start_s) in enumerate(b.audio_overlays):
            cmd += ["-i", str(opath)]
            ms = int(float(start_s) * 1000)
            filters.append(f"[{k + 2}:a]adelay={ms}|{ms}[ov{k}]")
            mix_inputs += f"[ov{k}]"
        n = 1 + len(b.audio_overlays)
        filters.append(
            f"{mix_inputs}amix=inputs={n}:duration=first:normalize=0[a]")
        cmd += ["-filter_complex", ";".join(filters),
                "-map", "0:v:0", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                "-shortest", str(out)]
        _run(cmd)
        return out

    def produce(self, stage: Stage) -> Path:
        # DEMO_RESUME_DIR: reuse finished beat mp4s from a prior run of the
        # same episode — good takes are never re-shot after a later beat fails
        resume = os.environ.get("DEMO_RESUME_DIR")
        resume_dir = Path(resume) if resume else None
        print(f"[{self.slug}] narration…")
        parts = []
        reused: set[int] = set()
        for i, b in enumerate(self.beats):
            prev = (resume_dir / f"beat_{i:02d}_{b.name}.mp4"
                    if resume_dir else None)
            if prev and prev.exists():
                dst = self.dir / prev.name
                dst.write_bytes(prev.read_bytes())
                b.audio_dur = ffprobe_duration(dst)
                reused.add(i)
                print(f"  beat {b.name}: REUSED from {resume_dir.name}")
                continue
            b.audio_path = self.dir / f"beat_{i:02d}_{b.name}.wav"
            b.audio_dur = generate_narration(b.narration, b.audio_path)
            print(f"  audio {b.name}: {b.audio_dur:.1f}s")
        print(f"[{self.slug}] recording {len(self.beats)} beats…")
        for i, b in enumerate(self.beats):
            if i in reused:
                parts.append(self.dir / f"beat_{i:02d}_{b.name}.mp4")
                continue
            print(f" beat {i + 1}/{len(self.beats)}: {b.name}")
            raw = self._record_beat(stage, i, b)
            parts.append(self._mux_beat(i, b, raw))
        # filter-graph concat (decode + re-encode): stream-copy concat
        # stitched AAC loosely and audio ran ~15s past video over 8 beats —
        # exact timestamps beat fast copies here
        final = self.dir / f"{self.slug}.mp4"
        cmd = ["ffmpeg", "-y"]
        fl = ""
        for k, p in enumerate(parts):
            cmd += ["-i", str(p)]
            fl += f"[{k}:v][{k}:a]"
        fl += f"concat=n={len(parts)}:v=1:a=1[v][a]"
        cmd += ["-filter_complex", fl, "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                str(final)]
        _run(cmd)
        report = {
            "final": str(final),
            "duration_s": ffprobe_duration(final),
            "beats": [
                {"name": b.name, "audio_s": round(b.audio_dur, 2)}
                for b in self.beats
            ],
        }
        (self.dir / "report.json").write_text(json.dumps(report, indent=2))
        print(f"[{self.slug}] DONE → {final}  ({report['duration_s']:.1f}s)")
        return final
