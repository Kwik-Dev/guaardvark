"""90-second pilot — Phase 0 gate for the walkthrough series.

Three beats: Dashboard intro -> Files desktop (open + drag a folder window)
-> theme switch (Fallout and back). Validates the full chain: narration-first
sync, per-beat takes/retakes, visible cursor, 1080p capture, assembly.

Run:  venv/bin/python pilot.py          (backend :5000 + frontend :5173 up,
                                         Xvfb :98 at 1920x1080 running)
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from director import Beat, Episode, Stage, FRONTEND

REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- beat actions

def reset_home(st: Stage):
    """Clean identical starting state for every take: fresh dashboard."""
    st.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()  # keep window focus with openbox
    st.page.wait_for_timeout(300)

def act_dashboard(st: Stage):
    # page pre-loaded before recording; just tour the cards with the cursor
    heads = st.page.locator("h6:visible")
    n = min(heads.count(), 3)
    if n == 0:
        st.cursor.glide(960, 400, dur=1.0)
    for i in range(n):
        st.hover_over(heads.nth(i), dur=0.9)
        time.sleep(0.7)


def verify_dashboard(st: Stage):
    assert st.path() in ("/", "/dashboard"), st.path()


def reset_files(st: Stage):
    """Folder-window state persists per user across sessions — close any
    leftover windows OFF-CAMERA (native clicks, before recording starts)."""
    st.page.goto(FRONTEND + "/documents", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    for _ in range(6):
        closes = st.page.locator("svg[data-testid='CloseIcon']")
        if closes.count() == 0:
            break
        try:
            closes.first.locator("xpath=ancestor::button[1]").click(timeout=3000)
        except Exception:
            break
        st.page.wait_for_timeout(400)
    reset_home(st)


def act_files(st: Stage):
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Videos", exact=True))
    time.sleep(0.8)
    icon = st.page.get_by_text("Videos", exact=True).first
    ibox = icon.bounding_box()
    # the double-click target is the folder GLYPH above the label — the label
    # text itself does not open the window
    lx, ly = st.screen_xy(icon)
    st.cursor.glide(lx, ly - 38, dur=0.9)
    time.sleep(0.3)
    st.cursor.double_click()
    # folder window opened => its window-scoped breadcrumb "Home" appears
    crumb = st.page.get_by_text("Home", exact=True).first
    crumb.wait_for(state="visible", timeout=10_000)
    time.sleep(1.5)
    # the title bar sits ~55px above the breadcrumb row; grab it there
    # (the header text itself is letter-spaced and unmatchable by text)
    cx, cy = st.screen_xy(st.page.get_by_text("Home", exact=True))
    hx, hy = cx + 300, cy - 55
    st.cursor.glide(hx, hy, dur=0.6)
    time.sleep(0.35)
    st.cursor.drag(hx + 380, hy + 320, dur=1.5)
    time.sleep(0.6)
    st.cursor.drag(hx + 120, hy + 110, dur=1.2)


def verify_files(st: Stage):
    assert "/documents" in st.path(), st.path()


def _choose_theme(st: Stage, name: str):
    # "Change Theme" chip opens the CHOOSE YOUR THEME dialog: pick a theme
    # card, then Apply
    chip = st.page.get_by_role("button", name=re.compile("change theme", re.I))
    st.glide_click(chip.first, dur=0.8)
    title = st.page.get_by_text(re.compile("choose your theme", re.I))
    title.first.wait_for(state="visible", timeout=8_000)
    time.sleep(0.5)
    card = st.page.get_by_text(re.compile(f"^{name}$", re.I))
    st.glide_click(card.first, dur=0.7)
    time.sleep(0.6)
    apply_btn = st.page.get_by_role("button", name=re.compile("^appl", re.I))
    st.glide_click(apply_btn.first, dur=0.6)
    title.first.wait_for(state="hidden", timeout=8_000)
    time.sleep(1.2)  # let the palette repaint on camera


def act_theme(st: Stage):
    st.nav_via_sidebar("Settings", "/settings",
                       st.page.get_by_text("Change Theme"))
    time.sleep(0.6)
    _choose_theme(st, "fallout")
    time.sleep(1.0)
    _choose_theme(st, "guaardvark")


def verify_theme(st: Stage):
    assert "/settings" in st.path(), st.path()


# --------------------------------------------------------------- narration

BEATS = [
    Beat(
        name="dashboard",
        narration=[
            "This is Guaardvark.",
            "A complete AI studio that runs on one desktop G P U.",
            "No cloud. No subscriptions. Nothing leaves your machine.",
            "",
            "You're looking at the dashboard. Live cards for the G P U, "
            "jobs, and projects.",
            "",
            "This is the pilot. A quick taste, before the full series.",
            "Let me show you two things.",
        ],
        action=act_dashboard,
        verify=verify_dashboard,
        reset=reset_home,
    ),
    Beat(
        name="files",
        narration=[
            "First. Your files get a real desktop, inside a browser tab.",
            "These are folders, sitting on a desktop surface.",
            "",
            "Double click.",
            "And a folder opens as a window.",
            "A real window. I can drag it around, resize it, stack it "
            "with others.",
            "",
            "P D Fs. Documents. Audio. Video.",
            "Everything opens right here, in the app.",
            "Nothing gets downloaded. Nothing gets uploaded.",
            "It's already home.",
        ],
        action=act_files,
        verify=verify_files,
        reset=reset_files,
    ),
    Beat(
        name="theme",
        narration=[
            "And because this is your machine, it looks how you want.",
            "Settings. Change theme.",
            "",
            "One click, and the whole studio goes Fallout green.",
            "One more, and we're back.",
            "",
            "That's the pilot.",
            "Twelve full episodes are coming.",
            "Chat with three brains. A film crew of five AI agents. "
            "Voice cloning, with consent built in. And a system that fixes "
            "its own code, overnight.",
            "",
            "One machine. No cloud.",
        ],
        action=act_theme,
        verify=verify_theme,
        reset=reset_home,
    ),
]


def main():
    ep = Episode("pilot", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2500)  # let cards hydrate before take 1
        size = stage.page.evaluate("() => [window.innerWidth, window.innerHeight]")
        print(f"stage viewport: {size}")
        if size[0] < 1900 or size[1] < 1070:
            raise RuntimeError(f"not fullscreen ({size}) — is openbox running on :98?")
        # warm the Vite dev server: first visit to a lazy route can take >10s
        # to transform — never pay that cost on camera
        for warm in ("/documents", "/settings", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()  # hand the window input focus before take 1
        stage.page.wait_for_timeout(400)
        final = ep.produce(stage)
        print(f"\nPILOT COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
