"""Episode 5 — A Million Pictures, One Prompt (≈4:30).

Media Director expansion, the lighthouse batch wall, model registry,
infographic speed-run, upscaling, and auto-filing into the library.

GPU cast: ComfyUI/offline image gen; Ollama ONLY for the Director beat
(recorded first, then unloaded). Audio Foundry stays OFF.
Assets in: pre-rendered lighthouse batch (see MASTER_TASKS asset session).

Run from scripts/demo_director/:  venv/bin/python episodes/ep05_images.py
CALIBRATE markers = selectors pending the probe pass on /batch-images, /images.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage, FRONTEND  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CONCEPT = ("One day in the life of a lighthouse keeper on a remote "
           "northern island")


def reset_home(st: Stage):
    st.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
    st.page.wait_for_timeout(1500)
    st.cursor.jump(960, 700)
    st.cursor.click()
    st.page.wait_for_timeout(300)


def sweep_windows(st: Stage):
    """Close persisted folder windows off-camera — they survive sessions and
    cover the desktop icons (a stale VIDEOS window ate the Images dclick)."""
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


def unload_ollama():
    import requests as rq
    try:
        rq.post("http://localhost:5000/api/model/unload",
                json={"all": True}, timeout=30)
    except Exception:
        pass


# ---------------------------------------------------------------- beats

def act_director(st: Stage):
    st.nav_via_sidebar("Image Gen", "/batch-images",
                       st.page.get_by_role("textbox").first)
    time.sleep(1.0)
    # CALIBRATE: concept input + Director expand control
    box = st.page.get_by_role("textbox").first
    st.glide_click(box, dur=0.8)
    st.cursor.type_text(CONCEPT, delay_ms=28)
    time.sleep(0.6)
    preview = st.page.get_by_role(
        "button", name=re.compile("preview prompts", re.I))
    st.glide_click(preview.first, dur=0.7)
    time.sleep(6.0)    # prompt preview renders while the VO tells the story


def act_wall(st: Stage):
    # proven Files-window path — the Media Library's section state is not
    # deterministic (a stale Videos view put Joker test renders on camera)
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Images", exact=True))
    icon = st.page.get_by_text("Images", exact=True).first
    lx, ly = st.screen_xy(icon)
    st.cursor.glide(lx, ly - 38, dur=0.9)
    time.sleep(0.3)
    st.cursor.double_click()
    st.page.get_by_text("Home", exact=True).first.wait_for(
        state="visible", timeout=10_000)
    time.sleep(1.2)
    # the list is VIRTUALIZED and sorts name-ascending — the newest batch row
    # is never rendered. Sort by Date (desc) so it lands at the top.
    for _ in range(2):
        if st.page.get_by_text("ImageBatch_08-13-2026_195115_088").count():
            break
        st.glide_click(st.page.get_by_text("Date", exact=True).first, dur=0.6)
        time.sleep(1.0)
    row = st.page.get_by_text("ImageBatch_08-13-2026_195115_088").first
    rx, ry = st.screen_xy(row)
    st.cursor.glide(rx, ry, dur=0.8)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(1.8)
    first = st.page.get_by_text(re.compile(r"ImageGen_.*_001", re.I)).first
    fx, fy = st.screen_xy(first)
    st.cursor.glide(fx, fy, dur=0.7)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(2.0)                          # fullscreen preview overlay
    for _ in range(4):                       # sibling paging
        st.cursor._xdo("key", "Right")
        time.sleep(1.5)
    st.cursor._xdo("key", "Escape")
    time.sleep(0.8)


def act_models(st: Stage):
    st.nav_via_sidebar("Settings", "/settings",
                       st.page.get_by_text("Change Theme"))
    time.sleep(0.8)
    btn = st.page.get_by_role("button", name=re.compile("image models", re.I))
    st.glide_click(btn.first, dur=0.9)
    time.sleep(2.5)                          # modal with the model registry
    st.cursor._xdo("key", "Escape")
    time.sleep(0.6)


def act_infographic(st: Stage):
    st.nav_via_sidebar("Media", "/images", st.page.get_by_role("tab").first)
    time.sleep(1.0)
    tab = st.page.get_by_role("tab", name=re.compile("infographic", re.I))
    st.glide_click(tab.first, dur=0.7)
    time.sleep(1.2)
    # CALIBRATE: infographic prompt field + generate
    box = st.page.get_by_role("textbox").first
    st.glide_click(box, dur=0.7)
    st.cursor.type_text("Five reasons to run AI locally", delay_ms=30)
    time.sleep(0.5)
    gen = st.page.get_by_role("button", name=re.compile("generate", re.I))
    st.glide_click(gen.first, dur=0.7)
    time.sleep(9.0)                          # flux-schnell: seconds, on camera


def act_upscale(st: Stage):
    st.nav_via_sidebar("Media", "/images", st.page.get_by_role("tab").first)
    time.sleep(1.0)
    tab = st.page.get_by_role("tab", name=re.compile("upscal", re.I))
    st.glide_click(tab.first, dur=0.7)
    time.sleep(2.0)                          # CALIBRATE: full flow next pass


def act_files(st: Stage):
    st.nav_via_sidebar("Files", "/documents",
                       st.page.get_by_text("Images", exact=True))
    icon = st.page.get_by_text("Images", exact=True).first
    lx, ly = st.screen_xy(icon)
    st.cursor.glide(lx, ly - 38, dur=0.9)
    time.sleep(0.3)
    st.cursor.double_click()
    time.sleep(3.0)


def v_batch(st: Stage):
    assert "/batch-images" in st.path(), st.path()


def v_media(st: Stage):
    assert "/images" in st.path(), st.path()


def v_settings(st: Stage):
    assert "/settings" in st.path(), st.path()


def v_files(st: Stage):
    assert "/documents" in st.path(), st.path()


def reset_with_ollama(st: Stage):
    import requests as rq
    try:
        rq.post("http://localhost:11434/api/generate",
                json={"model": "gemma4:latest", "prompt": "ok",
                      "stream": False}, timeout=120)
    except Exception as e:
        print(f"  ollama pre-warm failed: {e}")
    reset_home(st)


def reset_no_ollama(st: Stage):
    unload_ollama()
    sweep_windows(st)
    reset_home(st)


BEATS = [
    Beat(
        name="hook_director",
        narration=[
            "One concept in.",
            "",
            "The Media Director is an L L M art director. Give it a single "
            "idea, and it writes a set of distinct, connected prompts.",
            "Not the same image with different seeds.",
            "Different images, that belong together.",
            "",
            "Watch. One line about a lighthouse keeper.",
            "And it plans the whole day. Dawn. The cliff path. The storm. "
            "The lantern room.",
        ],
        action=act_director,
        verify=v_batch,
        reset=reset_with_ollama,
    ),
    Beat(
        name="wall",
        narration=[
            "Here's that plan, rendered.",
            "",
            "Ten frames. One story. Same island, same keeper, same weather "
            "rolling in.",
            "Every image a different shot, because every prompt was a "
            "different sentence.",
            "",
            "This is Z Image Turbo. A few seconds per frame, on one desktop "
            "G P U.",
        ],
        action=act_wall,
        verify=v_files,
        reset=reset_no_ollama,
    ),
    Beat(
        name="models",
        narration=[
            "The models are yours to choose.",
            "Z Image Turbo for speed. Flux Dev for maximum quality. Krea "
            "for realism.",
            "",
            "Every one of them downloads right here, with a progress bar. "
            "And every one of them runs locally.",
        ],
        action=act_models,
        verify=v_settings,
        reset=reset_home,
    ),
    Beat(
        name="infographic",
        narration=[
            "Need a graphic, not a photograph?",
            "",
            "Type. Generate.",
            "The infographic pipeline is tuned for one thing: seconds from "
            "idea to P N G.",
        ],
        action=act_infographic,
        verify=v_media,
        reset=reset_home,
    ),
    Beat(
        name="upscale",
        narration=[
            "And when a frame earns a bigger canvas, the upscaler takes it "
            "to four K, or eight.",
            "Eight different super-resolution models, including video, "
            "frame by frame.",
        ],
        action=act_upscale,
        verify=v_media,
        reset=reset_home,
    ),
    Beat(
        name="closer_files",
        narration=[
            "And everything you saw files itself into the library. "
            "Automatically.",
            "",
            "Still images are half the story.",
            "Next episode: the same card, making them move.",
            "",
            "One machine. No cloud.",
        ],
        action=act_files,
        verify=v_files,
        reset=reset_no_ollama,
    ),
]


def main():
    ep = Episode("ep05_images", BEATS,
                 out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        stage.page.goto(FRONTEND + "/", wait_until="load", timeout=30_000)
        stage.page.wait_for_timeout(2000)
        for warm in ("/batch-images", "/images", "/settings", "/documents", "/"):
            stage.page.goto(FRONTEND + warm, wait_until="load", timeout=60_000)
            stage.page.wait_for_timeout(2000)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        final = ep.produce(stage)
        print(f"\nEP05 COMPLETE: {final}")
    finally:
        stage.close()


if __name__ == "__main__":
    main()
