"""Episode 8 — Drop a Song, Get a Music Video (≈4:30).

The energy arc, the per-cut plan, the cost gate, and the finished beat-cut
suite — all on the completed asset run (music_video id 1, "EP08 asset —
Lighthouse Suite", scored by the series bed ACE-Step made in Episode 7).

GPU cast: none during the shoot (everything pre-rendered; kokoro narration
only). Shoot AFTER the asset run completes.

Run from scripts/demo_director/:  venv/bin/python episodes/ep08_musicvideo.py
CALIBRATE markers pending the /music-video page probe (run it post-asset).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
RUN_NAME = "EP08 asset — Lighthouse Suite"
MUSIC_BED = REPO / "data" / "uploads" / "Audio" / "series_music_bed.wav"


def reset_home(st: Stage):
    st.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()
    st.page.wait_for_timeout(300)


def nav_mv(st: Stage):
    st.nav_via_sidebar("Music Video", "/music-video",
                       st.page.get_by_text(re.compile("lighthouse suite", re.I)))
    time.sleep(1.0)


def open_run(st: Stage):
    nav_mv(st)
    row = st.page.get_by_text(re.compile("lighthouse suite", re.I)).first
    st.glide_click(row, dur=0.9)
    time.sleep(2.0)


# ---------------------------------------------------------------- beats

def act_hook(st: Stage):
    open_run(st)


def act_arc(st: Stage):
    open_run(st)
    # CALIBRATE: the energy-arc strip — hover across it left to right
    arc = st.page.get_by_text(re.compile("energy|arc", re.I))
    if arc.count():
        x, y = st.screen_xy(arc.first)
        st.cursor.glide(x - 250, y + 30, dur=0.8)
        st.cursor.glide(x + 250, y + 30, dur=2.6)   # sweep the strip
    time.sleep(1.5)


def act_plan(st: Stage):
    open_run(st)
    # CALIBRATE: the per-cut plan list — scroll through a few prompts
    plan = st.page.get_by_text(re.compile("plan|cut", re.I)).first
    x, y = st.screen_xy(plan)
    st.cursor.glide(x, y + 100, dur=0.8)
    for _ in range(3):
        st.cursor._xdo("click", "5")                # wheel down
        time.sleep(1.4)


def act_result(st: Stage):
    open_run(st)
    # CALIBRATE: final video player on the completed run — click play
    play = st.page.locator("video, [aria-label*='play' i]").first
    st.glide_click(play, dur=0.9)
    time.sleep(3.0)                                 # audio via overlay below


def v_mv(st: Stage):
    assert "/music-video" in st.path(), st.path()


BEATS = [
    Beat(
        name="hook",
        narration=[
            "Drop in a song.",
            "",
            "The system reads its tempo. Its beats. Its energy.",
            "Then an AI director writes a different shot for every cut.",
            "",
            "This song should sound familiar. It's the series music bed — "
            "the one the foundry composed back in episode seven.",
            "The system is scoring its own footage.",
        ],
        action=act_hook,
        verify=v_mv,
        reset=reset_home,
    ),
    Beat(
        name="arc",
        narration=[
            "This strip is the energy arc.",
            "Cool blue where the song breathes. Warm red where it drives.",
            "",
            "Cuts per minute follow the arc. Calm passages hold their "
            "shots. The build cuts faster.",
            "The music decides the rhythm of the edit. Literally.",
        ],
        action=act_arc,
        verify=v_mv,
        reset=reset_home,
    ),
    Beat(
        name="plan",
        narration=[
            "And here's the director's plan. One prompt per cut.",
            "Read a few. Dawn mist. The keeper at work. The storm "
            "building. The lantern igniting.",
            "",
            "Not one prompt with different seeds. Different sentences, "
            "telling one story.",
            "",
            "And notice what hasn't happened yet: no GPU time. Planning "
            "is free. You approve before the card lifts a finger.",
        ],
        action=act_plan,
        verify=v_mv,
        reset=reset_home,
    ),
    Beat(
        name="result",
        narration=[
            "Approved. Rendered. Assembled on the beat grid.",
            "",
            "Here's a taste of the finished suite.",
            "",
            "",
            "",
            "",
            "",
        ],
        action=act_result,
        verify=v_mv,
        reset=reset_home,
        audio_overlays=[(MUSIC_BED, 4.0)],
        min_hold=26.0,
    ),
    Beat(
        name="closer",
        narration=[
            "One song in. One film out. Cut to the beat, by a director "
            "that read the music first.",
            "",
            "Next episode: five AI crew members, a casting call, and a "
            "script. The film crew clocks in.",
            "",
            "One machine. No cloud.",
        ],
        action=act_hook,
        verify=v_mv,
        reset=reset_home,
    ),
]


def main():
    ep = Episode("ep08_musicvideo", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/music-video", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP08 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
