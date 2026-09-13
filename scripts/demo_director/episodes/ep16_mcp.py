"""Episode 16 — Plug In Anything (≈4:30). DRAFT rewrite — dry-run before shooting.

MCP doctor, install --dry-run, the policy, the index profile for clients, a
restricted Claude Code session calling a Guaardvark tool, approvals, and a
caveat recorded in one commit and fixed in a later one.

Every number spoken is read when this file loads (load_numbers): tool counts
from the same registry and policy the MCP server builds from, profile values
from the API the Settings chips render. Terminal beats re-check the printed
count against them, so narration cannot drift from the screen.

GPU cast: Ollama (the knowledge search embeds the query) + Audio Foundry;
ComfyUI only when CLIENT_WITH_IMAGE is on.
Requires: `python -m backend.mcp doctor` all PASS; the AcmeCorp corpus
indexed; CLIENT_DIR holding an mcp.json that launches the guaardvark server;
Claude Code logged in; backend restarted with private extensions parked.

Meant to replace episodes/ep16_mcp.py.
Run from scripts/demo_director/:  venv/bin/python episodes/ep16_mcp.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import helpers as H  # noqa: E402
from director import Beat, Episode, Stage  # noqa: E402
from helpers import (  # noqa: E402
    REPO, api_get, close_dialogs, goto, kill_stage_terminal, require,
    set_nav_chrome, stage_terminal, verify_no_private_names, verify_path)

PY = "backend/venv/bin/python"
QUIET = "2>/dev/null"          # the MCP CLI logs INFO lines to stderr

# Claude Code runs from a neutral folder holding only an mcp.json for the
# guaardvark server, with --restricted so no instructions, memory or hooks
# from the operator's own setup load. The prompt goes before the flags:
# --mcp-config is variadic and would swallow it.
CLIENT_DIR = Path("/var/tmp/guaardvark-ep16")
# The synthetic contract has a term, a scope and a renewal clause and no payment
# terms; asking for what it holds keeps the answer about the passages found.
SEARCH_QUERY = "AcmeCorp service agreement scope, term and renewal"
# A generate_image call after a search in the same session has stalled; keep
# the image off until that is understood. With it on, the prompt asks for
# wait_for_result=true because the MCP default queues and returns at once.
CLIENT_WITH_IMAGE = False
IMAGE_PROMPT = "a brass compass on a weathered sea chart, soft window light"


def fill_stage_terminal():
    """Size the stage terminal to the whole frame and move the pointer off its text.
    Bare Xvfb has no window manager, so ptyxis opens at its default size in a corner
    with the page showing around it; xdotool moves and sizes the window directly."""
    try:
        wid = H.focus_stage_terminal()
    except RuntimeError:
        return
    H._xdo("windowmove", wid, "0", "0")
    H._xdo("windowsize", wid, "1920", "1080")
    H._xdo("mousemove", "1900", "1060")


def stage_terminal(cmd: str, cwd: Path | None = None):
    """helpers.stage_terminal at double GTK scale, then the terminal fills the frame.
    At scale 1 a full-frame terminal leaves the text too small to read in the video.
    GDK_SCALE is set only while the terminal process starts (helpers copies the
    environment at spawn), so the browser on the same display is unaffected. Every
    beat calls this name, so the shared helper stays as the other episodes use it."""
    import os
    previous = os.environ.get("GDK_SCALE")
    os.environ["GDK_SCALE"] = "2"
    try:
        H.stage_terminal(cmd, cwd=cwd)
    finally:
        if previous is None:
            os.environ.pop("GDK_SCALE", None)
        else:
            os.environ["GDK_SCALE"] = previous
    fill_stage_terminal()

CAVEAT_COMMIT = "3fe3885"      # adds read_logs; its message records the path leak
FIX_COMMIT = "325ba18"         # doctor, install and read_logs print relative paths

# The policy beat names each denied category aloud; a change to the list must
# fail the load, not ship narration that describes the old list.
SPOKEN_DENY_CATEGORIES = {
    "desktop", "agent_control", "system", "test_execution", "browser",
    "mcp", "mcp_native",
}


# ----------------------------------------------------------- live numbers

_PROBE = r'''
import contextlib, dataclasses, io, json
with contextlib.redirect_stdout(io.StringIO()):
    from backend.tools.tool_registry_init import initialize_all_tools
    initialize_all_tools()
    from backend.mcp import installer
    from backend.mcp.config import load_config
    from backend.mcp.tools_adapter import collect_exposed_tools
    from backend.services.agent_tools import get_tool_registry
    cfg = load_config()
    exposed = collect_exposed_tools(cfg)
    ungated = collect_exposed_tools(dataclasses.replace(
        cfg, tools=dataclasses.replace(cfg.tools, hide_approval_required=False)))
    registry = get_tool_registry()
    names = registry.list_tools()
    approval = sorted(n for n in names
                      if getattr(registry.get_tool(n), "requires_approval", False))
    out = {
        "registered": len(names),
        "exposed": len(exposed),
        "exposed_needing_approval": sum(
            1 for base, _ in exposed if getattr(base, "requires_approval", False)),
        "approval": len(approval),
        "approval_gate_hides": len(ungated) - len(exposed),
        "hide_approval_required": cfg.tools.hide_approval_required,
        "deny_categories": list(cfg.tools.deny_categories),
        "clients_supported": len(installer.CLIENTS),
        "clients_detected": [c for c in installer.CLIENTS if installer._detect(c)],
    }
print("EP16_NUMBERS " + json.dumps(out))
'''


def load_numbers() -> dict:
    # The director runs in its own venv; the registry needs the backend's,
    # with .env exported the way a hand launch would.
    r = subprocess.run(
        ["bash", "-c", f"set -a; [ -f .env ] && . ./.env; set +a; exec {PY} -"],
        input=_PROBE, cwd=REPO, capture_output=True, text=True, timeout=300)
    line = next((ln for ln in reversed(r.stdout.splitlines())
                 if ln.startswith("EP16_NUMBERS ")), None)
    require(line, f"MCP probe printed no numbers (rc {r.returncode}): {r.stdout[-400:]}")
    n = json.loads(line.split(" ", 1)[1])

    profiles = {p["name"]: p for p in api_get("/api/settings/index_profiles")["profiles"]}
    require({"default", "local", "mcp"} <= set(profiles),
            f"index profiles on the API: {sorted(profiles)}")
    n["profiles"] = profiles

    require(n["registered"] > 0 and n["exposed"] > 0,
            "tool counts are zero: the registry did not initialise")
    require(set(n["deny_categories"]) == SPOKEN_DENY_CATEGORIES,
            f"deny categories changed: {n['deny_categories']}")
    require(n["hide_approval_required"] and n["exposed_needing_approval"] == 0,
            "an approval-required tool is exposed; the policy narration would be false")
    require(n["clients_detected"], "install detects no client on this machine")
    return n


N = load_numbers()
MCP = N["profiles"]["mcp"]
LOCAL = N["profiles"]["local"]
print("ep16 numbers:", {k: v for k, v in N.items() if k != "profiles"},
      "| mcp profile:", {k: MCP.get(k) for k in
                         ("top_k", "context_window_chunks", "chunk_chars", "rerank", "active")})

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, rest = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" and {words(rest)}" if rest else "")
    return f"{n:,}"


def Words(n: int) -> str:
    s = words(n)
    return s[:1].upper() + s[1:]


# --------------------------------------------------------------- terminal

def terminal_body() -> str:
    """What the current stage terminal has printed, minus script(1)'s header
    lines (they carry the command and are never on screen)."""
    if not H._TERMINAL_LOGS or not H._TERMINAL_LOGS[-1].exists():
        return ""
    raw = H._TERMINAL_LOGS[-1].read_bytes().decode("utf-8", "replace")
    return "\n".join(ln for ln in raw.splitlines()
                     if not ln.startswith(("Script started", "Script done")))


def wait_terminal(pattern: str, timeout: float) -> str:
    rx = re.compile(pattern)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = terminal_body()
        if rx.search(body):
            return body
        time.sleep(0.5)
    raise RuntimeError(f"terminal never printed /{pattern}/ within {timeout:.0f}s")


def plugin_running(pid: str) -> bool:
    return api_get("/api/plugins/status").get("status", {}).get(pid) == "running"


def reset_terminal(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/dashboard")
    time.sleep(0.5)


# ----------------------------------------------------------- beat: doctor

def reset_doctor(st: Stage):
    reset_terminal(st)
    r = subprocess.run([PY, "-m", "backend.mcp", "doctor"], cwd=REPO,
                       capture_output=True, text=True, timeout=300)
    require(r.returncode == 0, f"mcp doctor failed:\n{r.stdout[-800:]}")
    require(f"{N['exposed']} tools listed" in r.stdout,
            f"doctor does not list {N['exposed']} tools:\n{r.stdout[-800:]}")


def act_doctor(st: Stage):
    stage_terminal(f"{PY} -m backend.mcp doctor {QUIET}; sleep 40")
    wait_terminal(r"All checks passed", 90)
    time.sleep(4.0)


def v_doctor(st: Stage):
    require(f"{N['exposed']} tools listed" in terminal_body(),
            "doctor on screen disagrees with the narrated tool count")
    verify_no_private_names(st)


# ---------------------------------------------------------- beat: install

def act_install(st: Stage):
    stage_terminal(f"{PY} -m backend.mcp install --dry-run {QUIET}; sleep 30")
    wait_terminal(r"\[ dry\]", 60)
    time.sleep(4.0)


def v_install(st: Stage):
    rows = len(re.findall(r"\[ dry\]", terminal_body()))
    require(rows == len(N["clients_detected"]),
            f"dry run shows {rows} clients, narration says {len(N['clients_detected'])}")
    verify_no_private_names(st)


# ----------------------------------------------------------- beat: policy

POLICY_CMD = (
    f"{PY} -m backend.mcp list-tools {QUIET}"
    " | awk 'NR<=6 {print} END {print \"...\"; print NR \" tools exposed\"}'; echo; "
    "sed -n '/^DEFAULT_DENY_CATEGORIES/,/^]/p' backend/mcp/config.py; sleep 40")


def act_policy(st: Stage):
    stage_terminal(POLICY_CMD)
    wait_terminal(r"mcp_native", 60)
    time.sleep(4.0)


def v_policy(st: Stage):
    require(f"{N['exposed']} tools exposed" in terminal_body(),
            "list-tools on screen disagrees with the narrated count")
    verify_no_private_names(st)


# --------------------------------------------------------- beat: profiles

def knowledge_panel(st: Stage):
    return st.page.locator("#settings-knowledge")


def profile_chip(st: Stage, name: str):
    return knowledge_panel(st).get_by_role("switch", name=name, exact=True)


def reset_profiles(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/settings")
    profile_chip(st, "mcp").first.wait_for(state="visible", timeout=60_000)
    # Scroll off camera so the take opens with the panel in frame.
    knowledge_panel(st).get_by_text("Index profiles", exact=True).first \
        .scroll_into_view_if_needed(timeout=10_000)
    for name in ("default", "local", "mcp"):
        require(profile_chip(st, name).count(), f"no {name!r} profile chip")
    time.sleep(0.8)


def act_profiles(st: Stage):
    st.hover_over(knowledge_panel(st).get_by_text("Index profiles", exact=True), dur=0.9)
    time.sleep(1.2)
    for name in ("default", "local", "mcp"):   # mcp last: its tooltip stays up
        st.hover_over(profile_chip(st, name), dur=0.7)
        time.sleep(2.0)
    time.sleep(2.0)


def v_profiles(st: Stage):
    tip = st.page.get_by_role("tooltip")
    require(tip.count() and f"top_k {MCP['top_k']}" in tip.first.inner_text(),
            "the mcp chip tooltip is not showing the narrated top_k")
    verify_path(st, "/settings")


# ----------------------------------------------------------- beat: client

def client_command() -> str:
    tools = ["mcp__guaardvark__search_knowledge_base"]
    ask = (f"Use the guaardvark tools only. Search the knowledge base for: {SEARCH_QUERY}. "
           "Quote two short passages, each with the source file name it came from.")
    if CLIENT_WITH_IMAGE:
        tools.append("mcp__guaardvark__generate_image")
        ask += (" Then call generate_image with wait_for_result set to true and the "
                f"prompt: {IMAGE_PROMPT}. Report the image URL it returns.")
    # The prompt is shell-quoted with double quotes: keep it free of ", $ and `.
    assert not re.search(r'["$`]', ask), ask
    return (f'claude -p "{ask}" --restricted --strict-mcp-config --mcp-config mcp.json '
            f'--allowedTools "{",".join(tools)}"; sleep 45')


def reset_client(st: Stage):
    reset_terminal(st)
    require((CLIENT_DIR / "mcp.json").is_file(), f"no mcp.json in {CLIENT_DIR}")
    require(shutil.which("claude"), "Claude Code is not on PATH")
    require(plugin_running("ollama"),
            "Ollama is not running: the knowledge search cannot embed the query")
    if CLIENT_WITH_IMAGE:
        require(plugin_running("comfyui"), "ComfyUI is not running")


def act_client(st: Stage):
    stage_terminal(client_command(), cwd=CLIENT_DIR)
    # -p prints the whole answer when the session ends.
    wait_terminal(r"(?i)service[-_ ]agreement", 420 if CLIENT_WITH_IMAGE else 180)
    time.sleep(6.0)


def v_client(st: Stage):
    body = terminal_body()
    require(re.search(r"(?i)acmecorp|service[-_ ]agreement", body),
            "the client answer names no AcmeCorp source")
    require(not re.search(r"(?i)DEGRADED|Failed to connect to Ollama", body),
            "retrieval reported degraded; the passages are not real")
    if CLIENT_WITH_IMAGE:
        require("/api/batch-image/image/" in body, "no image URL in the client answer")
    verify_no_private_names(st)


# -------------------------------------------------------- beat: approvals

def reset_approvals(st: Stage):
    close_dialogs(st)
    kill_stage_terminal()
    set_nav_chrome(st, "software", path="/connections")
    # Only the Social tab (the default) carries the publish switches, and only
    # once publish settings have loaded.
    st.page.get_by_text("Require approval", exact=True).first.wait_for(
        state="visible", timeout=60_000)
    time.sleep(0.8)


def act_approvals(st: Stage):
    st.hover_over(st.page.get_by_text("Require approval", exact=True), dur=0.9)
    time.sleep(4.0)     # tooltip: chat or MCP always require approval
    st.glide_click(st.page.get_by_role("tab", name="MCP", exact=True), dur=0.8)
    time.sleep(2.5)
    goto(st, "/approvals", settle=2.5)
    pending = st.page.get_by_role("tab", name=re.compile(r"^Pending"))
    st.hover_over(pending, dur=0.8)
    time.sleep(2.0)


def v_approvals(st: Stage):
    require(st.page.get_by_role("tab", name=re.compile(r"^Pending")).count(),
            "the approvals page has no Pending tab")
    verify_path(st, "/approvals")


# ------------------------------------------------------------ beat: fixed

CAVEAT_CMD = (
    f"git log -1 --format=%b {CAVEAT_COMMIT} | tr '\\n' ' ' "
    "| grep -o 'Note read_logs[^.]*\\.'; echo; "
    f"git show --stat --format=%s {FIX_COMMIT} | head -8; sleep 30")


def act_fixed(st: Stage):
    stage_terminal(CAVEAT_CMD)
    wait_terminal(r"display_paths\.py", 30)
    time.sleep(4.0)


def v_fixed(st: Stage):
    body = terminal_body()
    require("crossing the MCP boundary" in body, "the recorded caveat is not on screen")
    require("display_paths.py" in body, "the fix commit is not on screen")
    verify_no_private_names(st)


# ------------------------------------------------------------ narration

def _client_narration() -> list[str]:
    lines = [
        "A client on camera. This is Claude Code, started in an empty folder "
        "with the Guaardvark server and nothing else: no project, no memory, "
        "no other servers. It may use exactly one Guaardvark tool, the "
        "knowledge base search." if not CLIENT_WITH_IMAGE else
        "A client on camera. This is Claude Code, started in an empty folder "
        "with the Guaardvark server and nothing else: no project, no memory, "
        "no other servers. It may use exactly two Guaardvark tools: the "
        "knowledge base search, and image generation.",
        "",
        "The corpus is synthetic, a company called AcmeCorp that does not "
        "exist. The search runs on this machine, and what comes back is "
        "passages with the file each one came from, not a summary. Claude "
        "sees the passages it asked for. The rest of the corpus stays here.",
    ]
    if CLIENT_WITH_IMAGE:
        lines += [
            "",
            "Then the image. It renders on this card, and what goes back to "
            "the client is an address on this machine's own server, not the "
            "picture.",
        ]
    return lines


def _profile_state() -> str:
    proj = MCP.get("projection") or {}
    if MCP.get("active") and proj.get("exists"):
        return "On this machine it is on and built."
    if MCP.get("active"):
        return "On this machine it is on, and builds with the next indexing run."
    return ("On this machine it is off. Turning it on marks it for the next "
            "indexing run; nothing is rebuilt on the spot.")


def _rerank(p: dict) -> str:
    return ", reranked" if p.get("rerank") else ""


BEATS = [
    Beat(name="doctor",
         narration=[
             f"{Words(N['exposed'])} tools. Any client that speaks the "
             "protocol. And a policy that says no by default.",
             "",
             "Start with doctor. It checks the interpreter and the S D K, "
             "builds the server, and runs a real handshake against a fresh "
             "server process. Then it reads every client config that points "
             "at Guaardvark. Pass or fail, one line each.",
         ],
         action=act_doctor, verify=v_doctor, reset=reset_doctor),
    Beat(name="install",
         narration=[
             f"Install knows {words(N['clients_supported'])} clients. It "
             "writes the server into every one it finds, and on this machine "
             f"it finds {words(len(N['clients_detected']))}. A config file is "
             "backed up before its first change, and any other servers "
             "already in it stay where they are.",
             "",
             "This is the dry run. It only says what it would do. Paths are "
             "written from your home folder and the checkout, because output "
             "like this ends up pasted into bug reports.",
         ],
         action=act_install, verify=v_install, reset=reset_terminal),
    Beat(name="policy",
         narration=[
             f"{Words(N['registered'])} tools are registered inside "
             f"Guaardvark. {Words(N['exposed'])} are exposed over M C P.",
             "",
             f"{Words(len(N['deny_categories']))} categories are denied by "
             "default: the virtual desktop, agent control, the shell, running "
             "arbitrary code, the browser, and the M C P tools themselves, "
             "including proxies to other servers. Opening one takes an "
             "explicit line in the config.",
             "",
             f"Then one more rule. {Words(N['approval'])} registered tools "
             "need a person's approval before they run, and none of them is "
             f"exposed. Take that rule away and {words(N['approval_gate_hides'])} "
             "more would appear.",
         ],
         action=act_policy, verify=v_policy, reset=reset_terminal),
    Beat(name="profiles",
         narration=[
             "Clients read differently from people. Settings, Knowledge, index "
             "profiles: one corpus, several projections of it, each built into "
             "its own table.",
             "",
             f"The M C P profile asks for {words(MCP['top_k'])} passages of "
             f"about {words(MCP['chunk_chars'])} characters, with "
             f"{words(MCP['context_window_chunks'])} neighbouring chunks of "
             f"context{_rerank(MCP)}, so a client can chain over them. The "
             f"local profile goes the other way: {words(LOCAL['top_k'])} "
             f"passages of about {words(LOCAL['chunk_chars'])} characters, "
             "for a small model's context window.",
             _profile_state(),
         ],
         action=act_profiles, verify=v_profiles, reset=reset_profiles),
    Beat(name="client",
         narration=_client_narration(),
         action=act_client, verify=v_client, reset=reset_client),
    Beat(name="approvals",
         narration=[
             "Anything that acts on the outside world waits for a person. "
             "Connections has a switch for whether publishing needs approval, "
             "and the rule under it is worth reading: requests from chat or "
             "M C P always require approval, whatever the switch says. The "
             "server enforces that, not just the tooltip.",
             "",
             "Outside M C P servers get their own tab. And the approvals page "
             "is where those requests wait.",
         ],
         action=act_approvals, verify=v_approvals, reset=reset_approvals),
    Beat(name="fixed",
         narration=[
             "One caveat, and what happened to it. When the log tool was "
             "added, its commit said so plainly: the tool returned the "
             "absolute path of the log it read. A machine path, crossing the "
             "M C P boundary.",
             "",
             "A later commit fixed it. Doctor, install and the log tool now "
             "print paths relative to the checkout and your home folder.",
             "",
             "What has not changed: the config a client starts the server "
             "from still holds the full path, because that is how the server "
             "is launched. It sits in your client's settings, on your machine.",
             "",
             "Next: five agents on one repository, every one of them in its "
             "own copy.",
         ],
         action=act_fixed, verify=v_fixed, reset=reset_terminal),
]


def main():
    ep = Episode("ep16_mcp", BEATS, out_root=REPO / "data" / "outputs" / "demos")
    stage = Stage()
    try:
        for warm in ("/", "/settings", "/connections", "/approvals"):
            goto(stage, warm, settle=2.0)
        stage.cursor.jump(960, 700)
        stage.cursor.click()
        print(f"\nEP16 COMPLETE: {ep.produce(stage)}")
    finally:
        kill_stage_terminal()
        stage.close()


if __name__ == "__main__":
    main()
