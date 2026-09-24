"""Approval prompt for `llx mcp client call`.

MCP tools the server policy gates (destructive, or with a write/delete/run
style name) ask before they run. In a terminal the user is prompted (default:
No). Without a terminal (--json, pipes, scripts) the call is refused unless
the tool matches an explicit ``--approve`` glob.
"""

import fnmatch
import json
import sys

from rich.panel import Panel
from rich.syntax import Syntax
from rich.console import Group
from rich.text import Text


def matches_any(tool_name: str, patterns: list[str] | None) -> bool:
    return any(fnmatch.fnmatchcase(tool_name or "", p) for p in (patterns or []))


def can_prompt() -> bool:
    """True when a person can answer: stdout is a terminal and a TTY is readable."""
    if not sys.stdout.isatty():
        return False
    if sys.stdin.isatty():
        return True
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def ask_yes_no(prompt: str) -> bool:
    """Read y/N from the terminal (works even when stdin is piped). Default: No."""
    try:
        if sys.stdin.isatty():
            answer = input(prompt)
        else:
            with open("/dev/tty") as tty_in, open("/dev/tty", "w") as tty_out:
                tty_out.write(prompt)
                tty_out.flush()
                answer = tty_in.readline()
    except (EOFError, KeyboardInterrupt, OSError):
        return False
    return answer.strip().lower() in ("y", "yes")


def render_request(request: dict) -> Panel:
    tool = request.get("tool", "?")
    title = f"{request['mcp_server']} / {request['mcp_tool']}" if request.get("mcp_server") else tool
    parts = [Text.assemble(("Tool: ", "bold"), (title, "bold yellow"))]
    if request.get("mcp_server"):
        parts.append(Text(f"(as {tool})", style="dim"))
    if request.get("reason"):
        parts.append(Text.assemble(("Why approval is needed: ", "dim"), request["reason"]))
    params = request.get("params") or {}
    if params:
        parts.append(Syntax(json.dumps(params, indent=2, default=str), "json", word_wrap=True))
    if request.get("timeout_s"):
        parts.append(Text(f"Denied automatically after {request['timeout_s']}s without an answer.", style="dim"))
    return Panel(Group(*parts), title="Allow this action?", border_style="yellow", expand=False)


def decide(request: dict, console, approve_patterns: list[str] | None, interactive: bool) -> tuple[bool, str]:
    """Return (approved, how) for one approval request."""
    tool = request.get("tool", "")
    if matches_any(tool, approve_patterns):
        return True, "approved by --approve"
    if not interactive:
        return False, "denied (no terminal to ask; pass --approve to allow)"
    console.print(render_request(request))
    approved = ask_yes_no("Approve? [y/N] ")
    return approved, "approved" if approved else "denied"

