#!/usr/bin/env python3
"""Minimal stdio MCP server used by the MCP client tests.

Tools:
  echo(text)          -> returns text (and registers an extra tool when text == "add_tool")
  add(a, b)           -> a + b
  fail()              -> raises, so the result carries isError=true
  slow(seconds)       -> sleeps, for timeout tests
  delete_thing(name)  -> annotated destructiveHint=true
  dump_env()          -> newline-separated environment variable NAMES
  crash()             -> terminates the process abruptly
"""

import asyncio
import os
import sys

from mcp.types import ToolAnnotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="echo-fixture", version="1.0.0")
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(name="echo-fixture")

READ_ONLY = ToolAnnotations.model_validate({"readOnlyHint": True})
DESTRUCTIVE = ToolAnnotations.model_validate({"destructiveHint": True})


@server.tool(description="Echo the given text back.")
def echo(text: str) -> str:
    if text == "add_tool":

        def extra(x: int) -> int:
            return x * 2

        server.add_tool(extra, name="extra", description="Doubles x.")
    # Something noisy on stderr, to prove stderr is drained and never blocks.
    print("echo called " + ("x" * 2000), file=sys.stderr, flush=True)
    return text


@server.tool(description="Add two integers.", annotations=READ_ONLY)
def add(a: int, b: int) -> int:
    return a + b


@server.tool(description="Always fails.")
def fail() -> str:
    raise RuntimeError("intentional failure")


@server.tool(description="Sleep for N seconds, then return.")
async def slow(seconds: float) -> str:
    await asyncio.sleep(seconds)
    return f"slept {seconds}"


@server.tool(description="Delete a thing.", annotations=DESTRUCTIVE)
def delete_thing(name: str) -> str:
    return f"deleted {name}"


@server.tool(description="List environment variable names visible to this server.",
             annotations=READ_ONLY)
def dump_env() -> str:
    return "\n".join(sorted(os.environ.keys()))


@server.tool(description="Exit the process immediately.")
def crash() -> str:
    os._exit(3)


@server.resource("fixture://greeting", description="A static greeting.")
def greeting() -> str:
    return "hello from fixture"


@server.prompt(description="Greet someone.")
def greet(name: str) -> str:
    return f"Please greet {name} warmly."


if __name__ == "__main__":
    server.run("stdio")
