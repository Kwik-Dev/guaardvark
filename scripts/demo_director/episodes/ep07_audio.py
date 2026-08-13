"""Episode 7 — The Voice Foundry (5:00).

The true story: the series narrator is a Piper voice cloned by Chatterbox in
this very repo. Consent gate, the whisper self-check, ACE-Step music, FX Lab.

GPU cast: Audio Foundry ONLY (10GB reservation evicts everything else).
Assets in: piper reference clip, A/B wavs, series music bed (asset session).
Assets out: this episode's shoot doubles as the audio asset session.

Run from scripts/demo_director/:  venv/bin/python episodes/ep07_audio.py
First run: use --dry-run styled calibration (see CALIBRATE notes) — Audio
Studio selectors are best-effort until the first probe pass.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND, DISPLAY  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
AUDIO = REPO / "data" / "uploads" / "Audio"
REF = REPO / "data" / "uploads" / "voice_references" / "piper-female-series-narrator.wav"

# A/B assets (same line, three engines) — regenerate in the asset session if
# these hashes rotate; see MASTER_TASKS walkthrough entry.
AB_PIPER = AUDIO / "ab_piper.wav"
AB_KOKORO = AUDIO / "ab_kokoro.wav"
AB_CLONE = AUDIO / "ab_clone.wav"
MUSIC_BED = AUDIO / "series_music_bed.wav"
FX_RAIN = AUDIO / "fx_rain_thunder.wav"


# ---------------------------------------------------------------- helpers

def nav_audio(st: Stage):
    st.nav_via_sidebar("Audio Studio", "/audio",
                       st.page.get_by_role("tab", name=re.compile("voice", re.I)))
    time.sleep(0.8)


def click_tab(st: Stage, name: str):
    st.glide_click(st.page.get_by_role("tab", name=re.compile(name, re.I)))
    time.sleep(1.0)


def terminal_shot(st: Stage, command: str, hold_s: float = 6.0):
    """Float a real terminal over the kiosk browser and run one command —
    used for the consent-403 and self-check beats. Openbox manages it."""
    proc = subprocess.Popen(
        ["x-terminal-emulator", "-e",
         f"bash -c \"{command}; sleep {int(hold_s + 2)}\""],
        env={"DISPLAY": DISPLAY, "PATH": "/usr/bin:/bin"},
    )
    time.sleep(hold_s + 2.5)
    proc.terminate()


def reset_audio(st: Stage):
    st.page.goto(FRONTEND + "/audio", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()
    st.page.wait_for_timeout(300)


# ---------------------------------------------------------------- beats

def pick_reference_clip(st: Stage):
    """Open the reference-clip picker ('...or pick a previously imported
    clip') and select the series narrator clip. (Calibrated 2026-08-13.)"""
    pick = st.page.locator(
        "xpath=(//*[contains(text(),'previously imported clip')]"
        "/following::*[@role='combobox'])[1]")
    st.glide_click(pick, dur=0.9)
    opt = st.page.locator("li[role='option']").filter(
        has_text=re.compile("piper-female", re.I))
    opt.first.wait_for(state="visible", timeout=8_000)
    st.glide_click(opt.first, dur=0.6)
    time.sleep(1.2)


def act_reference(st: Stage):
    nav_audio(st)
    pick_reference_clip(st)
    time.sleep(1.0)  # the audience hears the clip via the audio overlay


def act_consent(st: Stage):
    # the 403, live, in a real terminal over the app
    terminal_shot(
        st,
        "echo '$ curl -X POST .../generate/voice "
        "-d {reference_clip_path: /etc/passwd}'; "
        "curl -s -X POST http://localhost:5000/api/audio-foundry/generate/voice "
        "-H 'Content-Type: application/json' "
        "-d '{\"text\":\"gate test\",\"backend\":\"chatterbox\","
        "\"reference_clip_path\":\"/etc/passwd\"}' "
        "-w '\\nHTTP %{http_code}\\n'",
        hold_s=7.0,
    )


def act_clone_ab(st: Stage):
    # on-camera generation: pick the narrator clip, type a line, generate
    # ("GENERATE VOICEOVER"); the three-way A/B plays via audio_overlays
    nav_audio(st)
    pick_reference_clip(st)
    box = st.page.get_by_role("textbox").first   # "Enter script for narration..."
    st.glide_click(box, dur=0.8)
    st.cursor.type_text("Same identity. New range.", delay_ms=40)
    time.sleep(0.5)
    gen = st.page.get_by_role("button", name=re.compile("generate", re.I))
    st.glide_click(gen.first, dur=0.7)
    time.sleep(6.0)  # warm chatterbox: ~2-6s; waveform appears


def act_selfcheck(st: Stage):
    # the whisper read-check rejecting a doubled take — real checker, real
    # rejection, staged input (a line concatenated with itself)
    check = (
        "cd /home/llamax1/GX1/scripts/demo_director && "
        "./venv/bin/python -c \""
        "import sys; sys.path.insert(0, '.'); "
        "from director import _line_matches; from pathlib import Path; "
        "ok, heard = _line_matches('This is Guaardvark.', "
        "Path('/home/llamax1/GX1/data/demo_assets/ep07/doubled_line.wav')); "
        "print('expected: This is Guaardvark.'); "
        "print('heard:   ', heard); "
        "print('verdict: ', 'ACCEPT' if ok else 'REJECT - re-rolling seed')\""
    )
    terminal_shot(st, check, hold_s=8.0)


def act_music(st: Stage):
    nav_audio(st)
    click_tab(st, "music")
    # CALIBRATE: chip names / polish button / generate
    for chip in ("Dark", "Epic", "Cello"):
        c = st.page.get_by_role("button", name=re.compile(f"^{chip}$", re.I))
        if c.count():
            st.glide_click(c.first, dur=0.5)
            time.sleep(0.4)
    polish = st.page.get_by_role("button", name=re.compile("polish", re.I))
    if polish.count():
        st.glide_click(polish.first, dur=0.6)
        time.sleep(3.0)
    gen = st.page.get_by_role("button", name=re.compile("generate", re.I))
    st.glide_click(gen.first, dur=0.7)
    time.sleep(4.0)  # progress appears; the RESULT beat plays the real bed


def act_fx(st: Stage):
    nav_audio(st)
    click_tab(st, "fx")
    box = st.page.get_by_role("textbox").first
    st.glide_click(box, dur=0.7)
    st.cursor.type_text("rain on a tin roof, distant thunder", delay_ms=35)
    time.sleep(0.5)
    gen = st.page.get_by_role("button", name=re.compile("generate", re.I))
    st.glide_click(gen.first, dur=0.7)
    time.sleep(4.0)


def act_files(st: Stage):
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Audio", exact=True))
    icon = st.page.get_by_text("Audio", exact=True).first
    lx, ly = st.screen_xy(icon)
    st.cursor.glide(lx, ly - 38, dur=0.9)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(3.0)


def v_audio(st: Stage):
    assert "/audio" in st.path(), st.path()


def v_files(st: Stage):
    assert "/documents" in st.path(), st.path()


BEATS = [
    Beat(
        name="hook_reference",
        narration=[
            "The narrator of this series isn't a person.",
            "",
            "She started as a tiny, local text-to-speech model.",
            "Then this feature cloned her. Into the voice you're hearing "
            "right now.",
            "",
            "This thirteen-second clip is her entire origin story.",
            "The words are strange on purpose. Azure. Measured. Thirty "
            "birds. Together, they cover nearly every sound in English.",
        ],
        action=act_reference,
        verify=v_audio,
        reset=reset_audio,
        audio_overlays=[(REF, 14.0)],   # the reference itself, after the setup
        min_hold=28.0,
    ),
    Beat(
        name="consent",
        narration=[
            "Before any of that, one rule.",
            "",
            "Point the cloner at a file that was never consented, and the "
            "system refuses. Four oh three. No exceptions.",
            "Not a terms-of-service checkbox. A gate, enforced in code.",
        ],
        action=act_consent,
        verify=v_audio,
        reset=reset_audio,
    ),
    Beat(
        name="clone_ab",
        narration=[
            "Here's the same sentence, three ways.",
            "",
            "Piper. Fast, flat, dependable.",
            "",
            "Kokoro. Smoother, still fixed voices.",
            "",
            "And the Chatterbox clone. Same identity. New range.",
        ],
        action=act_clone_ab,
        verify=v_audio,
        reset=reset_audio,
        audio_overlays=[(AB_PIPER, 4.5), (AB_KOKORO, 9.5), (AB_CLONE, 15.0)],
        min_hold=22.0,
    ),
    Beat(
        name="selfcheck",
        narration=[
            "One more thing about that voice.",
            "Sometimes a clone stumbles. Repeats itself.",
            "",
            "So every line gets transcribed back, by a local whisper model, "
            "and checked against the script.",
            "A bad take never reaches your ears. It listens to itself, "
            "before you do.",
        ],
        action=act_selfcheck,
        verify=v_audio,
        reset=reset_audio,
    ),
    Beat(
        name="music",
        narration=[
            "Voices are half the foundry.",
            "",
            "Pick a mood with chips. Or type plain English, and the Polish "
            "pass rewrites it into the tag language the model actually "
            "understands. Before you spend a second of G P U time.",
            "",
            "Ace Step. Three and a half billion parameters. Full songs, "
            "with or without vocals. Right here.",
        ],
        action=act_music,
        verify=v_audio,
        reset=reset_audio,
    ),
    Beat(
        name="music_result",
        narration=[
            "Ninety seconds later.",
            "",
            "This is the track it made.",
            "It's the bed music for this entire series.",
            "",
            "",
            "",
        ],
        action=lambda st: time.sleep(0.5),
        verify=v_audio,
        reset=reset_audio,
        audio_overlays=[(MUSIC_BED, 3.0)],
        min_hold=20.0,
    ),
    Beat(
        name="fx",
        narration=[
            "And the effects lab.",
            "Rain on a tin roof. Distant thunder.",
            "",
            "Type it. Get it.",
        ],
        action=act_fx,
        verify=v_audio,
        reset=reset_audio,
        audio_overlays=[(FX_RAIN, 6.0)],
        min_hold=16.0,
    ),
    Beat(
        name="closer_files",
        narration=[
            "Everything the foundry makes files itself into your library. "
            "Automatically.",
            "",
            "One song. One voice. Made from each other.",
            "Next episode: drop that song into the system, and get a whole "
            "music video back.",
            "",
            "One machine. No cloud.",
        ],
        action=act_files,
        verify=v_files,
        reset=reset_audio,
    ),
]


def main():
    for asset in (REF, AB_PIPER, AB_KOKORO, AB_CLONE, MUSIC_BED, FX_RAIN):
        if not asset.exists():
            raise SystemExit(f"missing asset: {asset} — run the asset session first")
    ep = Episode("ep07_voice_foundry", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/audio", "/documents", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP07 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
