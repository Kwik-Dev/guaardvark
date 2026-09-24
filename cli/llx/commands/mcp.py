"""Wrap `python -m backend.mcp` — config snippets, install, doctor."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer

from llx import output
from llx.commands.system import _find_project_root
from llx.theme import make_console

console = make_console()
mcp_app = typer.Typer(help="MCP server install / doctor for agent clients", no_args_is_help=True)


def _find_checkout() -> Path | None:
    env_root = os.environ.get("GUAARDVARK_ROOT")
    if env_root:
        candidate = Path(env_root).resolve()
        if (candidate / "start.sh").is_file():
            return candidate
        return None

    candidate = Path(_find_project_root(os.getcwd()))
    if (candidate / "start.sh").is_file():
        return candidate

    curr = Path.cwd().resolve()
    for p in [curr, *curr.parents]:
        if (p / "start.sh").is_file():
            return p
    return None


_CHECKOUT_NOT_FOUND_MSG = (
    "Guaardvark checkout not found (looked for start.sh via GUAARDVARK_ROOT or cwd); "
    "a checkout is required (git clone https://github.com/guaardvark/guaardvark + ./start.sh).\n"
)


def _python_and_root() -> tuple[str, Path | None]:
    root = _find_checkout()
    if root is None:
        return sys.executable, None
    venv_py = root / "backend" / "venv" / "bin" / "python"
    py = str(venv_py) if venv_py.is_file() else sys.executable
    return py, root


def _run_mcp(args: list[str]) -> int:
    py, root = _python_and_root()
    if root is None:
        sys.stderr.write(_CHECKOUT_NOT_FOUND_MSG)
        return 1
    cmd = [py, "-m", "backend.mcp", *args]
    result = subprocess.run(cmd, cwd=str(root))
    return result.returncode


@mcp_app.command("serve")
def mcp_serve(
    http: bool = typer.Option(False, "--http", help="Run MCP server over HTTP instead of stdio"),
):
    """Run the MCP server (stdio by default, or HTTP with --http)."""
    py, root = _python_and_root()
    if root is None:
        sys.stderr.write(_CHECKOUT_NOT_FOUND_MSG)
        raise typer.Exit(1)

    cmd = [py, "-m", "backend.mcp", "http"] if http else [py, "-m", "backend.mcp"]
    os.chdir(str(root))
    os.execv(py, cmd)


@mcp_app.command("config")
def mcp_config(
    client: str = typer.Option(
        ...,
        "--client",
        "-c",
        help="claude-desktop | claude-code | cursor | zed",
    ),
):
    """Print the JSON snippet to paste into an MCP client config."""
    code = _run_mcp(["config", "--client", client])
    raise typer.Exit(code)


@mcp_app.command("install")
def mcp_install(
    client: list[str] = typer.Option(
        None,
        "--client",
        "-c",
        help="Client to configure (repeatable). Default: every detected client.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Write the Guaardvark MCP entry into agent client configs."""
    args = ["install"]
    if dry_run:
        args.append("--dry-run")
    for c in client or []:
        args.extend(["--client", c])
    raise typer.Exit(_run_mcp(args))


@mcp_app.command("doctor")
def mcp_doctor():
    """Diagnose MCP server + client config."""
    raise typer.Exit(_run_mcp(["doctor"]))


@mcp_app.command("list-tools")
def mcp_list_tools():
    """Print tools the MCP server exposes."""
    raise typer.Exit(_run_mcp(["list-tools"]))


# `llx mcp client ...`: the external MCP servers the agent itself uses.
from llx.commands.mcp_client import mcp_client_app  # noqa: E402

mcp_app.add_typer(mcp_client_app, name="client")
