"""Episode 18 — Your Agent, Your Studio (≈4:30; 90 s social cut). DRAFT — dry-run before shooting.

Two lines install the plugin in Claude Code; one sentence asks for a music video; the
agent runs the skill: preflight, queue, the approval gate, the swap on the GPU, the
finished file, one honest caveat.

GPU cast: ComfyUI (wan22-5b image-to-video) + Audio Foundry off camera (the song is
pre-produced in an asset session). Requires: `python -m backend.mcp doctor` all PASS;
Claude Code logged in on the stage display; a 20–30 s song document in the library
(EP18_SONG_DOC_ID); backend restarted with private extensions parked; the plugin
installable from GitHub (`claude plugin marketplace add guaardvark/guaardvark`).

Numbers are read, not typed: skill count from .agents/skills, tool counts from
`python -m backend.mcp list-tools`, the card from inspect_gpu.

Run from scripts/demo_director/:  venv/bin/python episodes/ep18_agent.py
Dry-run:                          venv/bin/python dryrun.py episodes/ep18_agent.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director import Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, focus_window, goto, kill_stage_terminal, require,
    set_nav_chrome, stage_claude, stage_terminal, type_into_stage_terminal,
    verify_no_private_names, verify_path)

PLUGIN_TOOLS = "mcp__plugin_guaardvark_guaardvark__*,Skill"

PY = "backend/venv/bin/python"
SONG_DOC_ID = os.environ.get("EP18_SONG_DOC_ID", "")
STYLE = "grainy 16 millimetre, neon rain on wet asphalt, slow dolly, 1984"

# Claude Code on camera runs from a throwaway config that holds only a login, in
# a neutral folder, so none of the operator's settings, memory, hooks, plugins or
# MCP servers load and the plugin install never touches the real config. The
# plugin's MCP launcher finds the checkout through GUAARDVARK_ROOT, so the path is
# never typed on camera. Every claude call below inherits both.
CLIENT_DIR = Path(os.environ.get("EP18_CLIENT_DIR", "/var/tmp/guaardvark-ep18"))
CLAUDE_CONFIG = CLIENT_DIR / "claude-config"
os.environ["CLAUDE_CONFIG_DIR"] = str(CLAUDE_CONFIG)
os.environ["GUAARDVARK_ROOT"] = str(REPO)

# ---- numbers, read at load ---------------------------------------------------
SKILLS = sorted(p.name for p in (REPO / ".agents" / "skills").iterdir()
                if (p / "SKILL.md").is_file() and not p.name.startswith(("_", ".")))
# -v: one-shot CLI commands log warnings only, and the counts are an INFO line.
_lt = subprocess.run([PY, "-m", "backend.mcp", "list-tools", "-v"], cwd=REPO,
                     capture_output=True, text=True, timeout=300)
_m = re.search(r"exposing (\d+) of (\d+) registered", _lt.stderr + _lt.stdout)
EXPOSED, REGISTERED = (int(_m.group(1)), int(_m.group(2))) if _m else (0, 0)
require(len(SKILLS) == 15, f"expected 15 skills, found {len(SKILLS)}: {SKILLS}")
require(EXPOSED > 0, "list-tools did not report the exposed count")

WORDS = {15: "fifteen", 46: "forty-six", 47: "forty-seven", 48: "forty-eight",
         90: "ninety", 91: "ninety-one", 92: "ninety-two"}
def say(n: int) -> str:
    return WORDS.get(n, str(n))


# ---- resets / actions -------------------------------------------------------
def reset_terminal(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


def reset_install(st: Stage):
    reset_terminal(st)
    require((CLAUDE_CONFIG / ".credentials.json").is_file(),
            f"no Claude Code login in the throwaway config {CLAUDE_CONFIG}")
    # A clean plugin state so the install is real on camera; scoped to the
    # throwaway config by CLAUDE_CONFIG_DIR.
    subprocess.run(["claude", "plugin", "uninstall", "guaardvark@guaardvark"],
                   capture_output=True, text=True)
    subprocess.run(["claude", "plugin", "marketplace", "remove", "guaardvark"],
                   capture_output=True, text=True)


def act_install(st: Stage):
    stage_terminal(
        "claude plugin marketplace add guaardvark/guaardvark && "
        "claude plugin install guaardvark@guaardvark; sleep 30", cwd=CLIENT_DIR)
    time.sleep(16.0)


def v_installed(st: Stage):
    r = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True)
    require("guaardvark@guaardvark" in r.stdout, "plugin not listed after install")
    verify_no_private_names(st)


_BASELINE = {"music_video_id": 0}


def reset_ask(st: Stage):
    reset_terminal(st)
    require(SONG_DOC_ID, "EP18_SONG_DOC_ID is not set (a 20–30 s song in the library)")
    rows = api_get("/api/music-video").get("music_videos") or []
    _BASELINE["music_video_id"] = max((int(r.get("id") or 0) for r in rows), default=0)
    ok = api_get("/api/health")
    require(ok.get("status") == "ok", "backend not healthy")
    plugins = api_get("/api/plugins/status").get("status", {})
    require(plugins.get("comfyui") == "running", "ComfyUI plugin must be running")
    # Song analysis (the Director) needs both; a stopped one fails the project
    # and the agent starts debugging the checkout on camera.
    for pid in ("video_editor", "ollama"):
        require(plugins.get(pid) == "running", f"the {pid} plugin must be running for song analysis")


def act_ask(st: Stage):
    # One interactive session, kept alive across the ask, gate and file beats,
    # so the skill loading, the streamed tool calls and the permission prompt
    # are all on camera. The agent must stop at the gate; it never approves.
    # auto: the setup skill loads its health and plugin checks through shell
    # injections, which manual mode stops at a permission prompt.
    stage_claude(PLUGIN_TOOLS, cwd=CLIENT_DIR, extra="--permission-mode auto", boot=8.0)
    type_into_stage_terminal(
        f"Make a music video from song document {SONG_DOC_ID} in this style: {STYLE}. "
        "Follow the guaardvark skills. Stop at the approval gate and tell me the cost "
        "before you ask me to approve.", delay_ms=40)
    # Hold until the agent has started the project, then let its report print.
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        rows = api_get("/api/music-video").get("music_videos") or []
        if any(int(r.get("id") or 0) > _BASELINE["music_video_id"] for r in rows):
            break
        time.sleep(3.0)
    time.sleep(20.0)


def v_ask(st: Stage):
    # The sentence only counts if the agent actually started a project; a
    # session stuck at a login or trust prompt would otherwise pass here and
    # hang at the gate.
    project = newest_music_video()
    require(int(project.get("id") or 0) > _BASELINE["music_video_id"],
            "the agent did not create a music video project")
    verify_no_private_names(st)


def reset_studio(st: Stage):
    # Keep the Claude session alive; bring the Studio window forward.
    close_dialogs(st)
    set_nav_chrome(st, "software", path="/music-video")
    focus_window("Chromium|Guaardvark")
    time.sleep(1.5)


def newest_music_video() -> dict:
    """The project the agent created: the list route answers newest first."""
    body = api_get("/api/music-video")
    rows = body if isinstance(body, list) else (
        body.get("music_videos") or body.get("videos") or body.get("items") or [])
    require(rows, "no music video project exists yet")
    return rows[0]


def act_studio(st: Stage):
    # The page selects nothing on load; open the agent's project so its plan shows.
    project = newest_music_video()
    st.glide_click(st.page.get_by_text(project["name"], exact=True).first, dur=0.9)
    st.page.get_by_role("heading", name=project["name"]).first.wait_for(
        state="visible", timeout=15_000)
    time.sleep(6.0)


def reset_keep_session(st: Stage):
    # The interactive session from the ask beat stays up.
    close_dialogs(st)


def v_gate(st: Stage):
    stage = newest_music_video().get("current_stage")
    require(stage in ("generating", "assembling", "complete"),
            f"the approval did not start generation (stage {stage})")
    verify_no_private_names(st)


def reset_gate(st: Stage):
    # The approve route answers 409 until analysis has written the plan, so the
    # take starts only once the project is at the gate with cuts.
    close_dialogs(st)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        project = newest_music_video()
        if project.get("current_stage") == "awaiting_approval" and project.get("cut_count"):
            return
        time.sleep(3.0)
    raise RuntimeError("precondition failed: the music video never reached the approval gate")


def act_gate(st: Stage):
    # The user answers the agent in the same session: yes. The agent calls the
    # approve route itself; the take holds until generation has actually begun,
    # then the GPU HUD shows the swap.
    type_into_stage_terminal("Yes, approve it.", delay_ms=50)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if newest_music_video().get("current_stage") in ("generating", "assembling", "complete"):
            break
        time.sleep(3.0)
    time.sleep(6.0)
    focus_window("Chromium|Guaardvark")
    goto(st, "/dashboard", settle=2.0)
    time.sleep(8.0)


def reset_file(st: Stage):
    # Rendering takes minutes per clip; the take starts once the video is
    # assembled, so the agent's poll returns the file instead of dead air.
    close_dialogs(st)
    project = newest_music_video()
    deadline = time.monotonic() + max(900, int(project.get("cut_count") or 0) * 120)
    while time.monotonic() < deadline:
        project = newest_music_video()
        if project.get("current_stage") == "complete":
            return
        require(not str(project.get("status") or "").startswith("failed"),
                f"the music video failed at {project.get('current_stage')}: {project.get('status')}")
        time.sleep(10.0)
    raise RuntimeError("precondition failed: the music video did not finish rendering")


def act_file(st: Stage):
    type_into_stage_terminal(
        "Poll until the music video is finished and give me the file.", delay_ms=40)
    time.sleep(60.0)
    focus_window("Chromium|Guaardvark")
    goto(st, "/media", settle=2.5)
    time.sleep(6.0)


def act_caveat(st: Stage):
    # The honesty beat, same session: a clamp or a refusal read back verbatim.
    type_into_stage_terminal(
        "Generate a 1024x1024 image of a paper boat with generate_image at 1 step, then "
        "poll it and tell me verbatim what the server changed or refused.", delay_ms=40)
    time.sleep(50.0)


def v_any(st: Stage):
    verify_no_private_names(st)


BEATS = [
    Beat(name="install",
         narration=[
             "Two lines. No clone.",
             "",
             f"The marketplace is the repository itself. The install brings {say(len(SKILLS))} "
             "skills and the M C P server, which runs from your own checkout.",
         ],
         action=act_install, verify=v_installed, reset=reset_install),
    Beat(name="ask",
         narration=[
             "One sentence. A song, a style, and the word skills.",
             "",
             "The agent names the skill it is following, checks the backend, the plugins and "
             "the models, and asks the card what it is.",
             f"{say(EXPOSED)} tools are exposed of {say(REGISTERED)} registered. The rest stay "
             "behind a policy that says no by default.",
         ],
         action=act_ask, verify=v_ask, reset=reset_ask),
    Beat(name="studio",
         narration=[
             "Queued is not done. The Director has analysed the song, cut it on the beat, and "
             "written one prompt per cut. Nothing has touched the G P U yet.",
         ],
         action=act_studio, verify=lambda st: verify_path(st, "/music-video"),
         reset=reset_studio),
    Beat(name="gate",
         narration=[
             "The gate. The agent says how many cuts, how many seconds each, which model, "
             "and how long. Then it asks.",
             "",
             "Yes. The approve route fires. Watch the card: the chat model leaves, the video "
             "model arrives. One heavy job at a time.",
             "One machine. No cloud.",
         ],
         action=act_gate, verify=v_gate, reset=reset_gate),
    Beat(name="file",
         narration=[
             "The agent polls by batch i d until the status route says completed, and hands "
             "back a file, not a promise.",
             "It is in the media library, on this disk.",
         ],
         action=act_file, verify=lambda st: verify_path(st, "/media"), reset=reset_file),
    Beat(name="caveat",
         narration=[
             "One honest beat. One step is below what this model needs: measured on this "
             "machine, one step is noise and two is clean. The server raises it to two, "
             "says so, and the agent reads that back to you word for word.",
             "",
             f"{say(len(SKILLS))} skills. Your G P U. Your agent.",
         ],
         action=act_caveat, verify=v_any, reset=reset_keep_session),
]


def main():
    ep = Episode("ep18_agent", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/music-video", "/media", "/dashboard"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP18 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()


if __name__ == "__main__":
    main()
