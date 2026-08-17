"""Episode 9 — A Film Crew That Never Sleeps (≈4:30).

Five AI crew members, one logline, two human gates. Cast is Ivy (subject 1,
trigger ivyx, Z-Image LoRA Ivy_v1). Production id 1:
"EP09 asset — Ivy Relights the Lantern" — 6 shots, storyboards on disk.

GPU cast during shoot: none (kokoro narration only) once the production has
rendered. If render is still in flight, the storyboard grid is the visual.

Run from scripts/demo_director/:  venv/bin/python episodes/ep09_filmcrew.py
Requires Xvfb :98 @ 1920x1080, production 1 at least through storyboards.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PROD_NAME = "Ivy Relights the Lantern"


def reset_home(st: Stage):
    st.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()
    st.page.wait_for_timeout(300)


def nav_crew(st: Stage):
    st.nav_via_sidebar("Film Crew", "/film-crew",
                       st.page.get_by_text(re.compile("film crew|productions", re.I)))
    time.sleep(1.0)


def open_prod(st: Stage):
    nav_crew(st)
    row = st.page.get_by_text(re.compile("ivy relights", re.I)).first
    row.wait_for(state="visible", timeout=12_000)
    st.glide_click(row, dur=0.9)
    st.page.get_by_text("Pipeline Progress", exact=True).first.wait_for(
        state="visible", timeout=12_000)
    time.sleep(1.2)


def _hover_label(st: Stage, pattern, dur=0.8):
    loc = st.page.get_by_text(pattern).first
    if loc.count():
        st.hover_over(loc, dur=dur)
        return True
    return False


def act_hook(st: Stage):
    open_prod(st)
    _hover_label(st, re.compile(r"^Screenwriting$"), 0.7)
    time.sleep(0.6)
    _hover_label(st, re.compile(r"^Casting$"), 0.7)
    time.sleep(0.6)
    _hover_label(st, re.compile(r"^Complete$"), 0.9)
    time.sleep(1.5)


def act_script(st: Stage):
    open_prod(st)
    script = st.page.get_by_text("Script", exact=True).first
    if script.count():
        script.scroll_into_view_if_needed(timeout=5_000)
        st.hover_over(script, dur=0.7)
        time.sleep(0.6)
    for pat in (
        re.compile(r"ESTABLISHING SHOT", re.I),
        re.compile(r"MEDIUM SHOT", re.I),
        re.compile(r"OVER THE SHOULDER", re.I),
    ):
        loc = st.page.get_by_text(pat)
        if loc.count():
            loc.first.scroll_into_view_if_needed(timeout=4_000)
            st.hover_over(loc.first, dur=0.8)
            time.sleep(0.9)
    time.sleep(1.2)


def act_cast(st: Stage):
    nav_crew(st)
    tab = st.page.get_by_role("tab", name=re.compile(r"cast library", re.I))
    tab.first.wait_for(state="visible", timeout=10_000)
    st.glide_click(tab.first, dur=0.8)
    time.sleep(1.4)
    ivy = st.page.get_by_text("Ivy", exact=True).first
    ivy.wait_for(state="visible", timeout=10_000)
    st.hover_over(ivy, dur=0.9)
    time.sleep(0.8)
    _hover_label(st, re.compile(r"^trained$"), 0.8)
    time.sleep(0.5)
    _hover_label(st, "ivyx", 0.9)
    time.sleep(1.8)


def act_lora(st: Stage):
    st.page.goto(FRONTEND + "/cast/1", wait_until="load", timeout=30_000)
    st.page.get_by_text("Ivy", exact=True).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(1.2)
    _hover_label(st, re.compile(r"ivyx", re.I), 0.8)
    time.sleep(0.7)
    _hover_label(st, re.compile(r"LoRA rank", re.I), 0.8)
    time.sleep(0.7)
    trained = st.page.get_by_text(re.compile(r"trained", re.I))
    if trained.count():
        st.hover_over(trained.first, dur=0.9)
    refs = st.page.locator("img")
    if refs.count() > 1:
        st.hover_over(refs.nth(1), dur=0.8)
        time.sleep(0.8)
        if refs.count() > 3:
            st.hover_over(refs.nth(min(3, refs.count() - 1)), dur=0.8)
    time.sleep(1.8)


def act_boards(st: Stage):
    open_prod(st)
    _hover_label(st, re.compile(r"^Cinematography$"), 0.8)
    time.sleep(0.6)
    heading = st.page.get_by_text("Storyboard", exact=True).first
    if heading.count():
        heading.scroll_into_view_if_needed(timeout=5_000)
        st.hover_over(heading, dur=0.7)
    imgs = st.page.locator("img")
    n = min(imgs.count(), 4)
    for i in range(n):
        st.hover_over(imgs.nth(i), dur=0.7)
        time.sleep(0.9)
    for _ in range(2):
        st.cursor._xdo("click", "5")
        time.sleep(1.0)
    time.sleep(1.0)


def act_closer(st: Stage):
    open_prod(st)
    _hover_label(st, re.compile(r"^Approval$"), 0.7)
    time.sleep(0.4)
    _hover_label(st, re.compile(r"^Rendering$"), 0.7)
    time.sleep(0.4)
    _hover_label(st, re.compile(r"^Complete$"), 0.9)
    time.sleep(0.8)
    heading = st.page.get_by_text("Storyboard", exact=True).first
    if heading.count():
        heading.scroll_into_view_if_needed(timeout=5_000)
        st.hover_over(heading, dur=0.7)
    imgs = st.page.locator("img")
    if imgs.count():
        st.hover_over(imgs.first, dur=0.8)
        time.sleep(1.4)
    # Tease Ep 10 without claiming a Shotcut project we didn't compose.
    try:
        st.nav_via_sidebar(
            "Video Editor", "/video-editor",
            st.page.get_by_text(re.compile(r"video editor|timeline|plan", re.I)),
        )
        time.sleep(2.2)
    except Exception as e:
        print(f"  closer: video editor nav skipped ({e})")
        time.sleep(1.0)
    # Land back on Film Crew so verify() matches the rest of the episode.
    nav_crew(st)
    time.sleep(1.0)


def v_crew(st: Stage):
    assert "/film-crew" in st.path(), st.path()


def v_cast(st: Stage):
    assert "/cast" in st.path(), st.path()


BEATS = [
    Beat(
        name="hook",
        narration=[
            "Screenwriter. Casting director. Cinematographer. "
            "Storyboard artist. Editor.",
            "",
            "Five AI crew members. One three-line logline.",
            "And the only person on set is you. Exactly twice.",
        ],
        action=act_hook,
        verify=v_crew,
        reset=reset_home,
    ),
    Beat(
        name="screenwriter",
        narration=[
            "The logline goes in.",
            "Ivy. A forest spirit. A dead lantern. A grove that has to live.",
            "",
            "The screenwriter breaks it into scenes and shots. Structured. "
            "Not a paragraph. A plan.",
        ],
        action=act_script,
        verify=v_crew,
        reset=reset_home,
    ),
    Beat(
        name="casting",
        narration=[
            "Gate one. You pick the cast.",
            "",
            "Ivy is already in the library. Trained. Locked.",
            "The other names — the grove, the path, the lantern — generate "
            "inline. They don't need a face that persists.",
        ],
        action=act_cast,
        verify=v_crew,
        reset=reset_home,
    ),
    Beat(
        name="lora",
        narration=[
            "Here's how she got that face.",
            "Five reference stills. A vision-built identity bible. "
            "Rank sixteen. About thirty-two megabytes.",
            "",
            "The system looked at the photos and wrote what must stay "
            "true in every shot. Green skin. Dark curls. Ivy in the hair.",
        ],
        action=act_lora,
        verify=v_cast,
        reset=reset_home,
    ),
    Beat(
        name="storyboard",
        narration=[
            "The cinematographer plans lenses and frames.",
            "Then the storyboard artist fills the grid.",
            "",
            "And a local vision model looks at each still. On-model shots "
            "pass. Doubtful ones escalate. The AI reviews its own work, "
            "and knows when to ask.",
        ],
        action=act_boards,
        verify=v_crew,
        reset=reset_home,
    ),
    Beat(
        name="closer",
        narration=[
            "Gate two. You approve. Then the editor animates each still "
            "into a clip and cuts them together.",
            "",
            "Next episode: the editor that shows its work.",
            "",
            "One machine. No cloud.",
        ],
        action=act_closer,
        verify=v_crew,
        reset=reset_home,
    ),
]


def main():
    ep = Episode("ep09_filmcrew", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/film-crew", "/cast", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP09 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
