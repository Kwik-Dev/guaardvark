"""Search this checkout's source by meaning or keyword.

One tool name for "search the code", whatever is behind it. With the
zvec-grep plugin connected (plugins/zvec_grep) the query runs hybrid: vector
plus keyword over one local index, and a plain-language question such as
"where is it decided whether a model supports thinking" comes back with the
defining function first. Without it the query degrades to the repository's
regex search, so the tool never advertises more than the machine can do.

The workspace root is never asked of the model: the MCP tool behind this one
requires an absolute root, and in the first trial the model guessed, ran
`pwd`, and burned two turns finding it. It comes from the request's
project_root, else the configured Guaardvark root.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

MCP_SERVER = "zvec_grep"
MCP_TOOL = "zvec_grep_search"
DEFAULT_LIMIT = 8
# What the model gets to read per call. zg returns ~1.3 KB per hit with source
# lines; eight hits is enough to answer and small enough to leave room for the
# answer in the context window.
MAX_OUTPUT_CHARS = 12000


def _default_root() -> str:
    from backend.services.guarded_code_service import default_repo_root
    return str(default_repo_root())


def _resolve_root(explicit: Optional[str], agent_context: Optional[Dict[str, Any]]) -> str:
    """The checkout to search. A relative ``root`` ("frontend/src") narrows the
    project root rather than failing: the model reaches for that when it wants
    to look in one area, and the index is per checkout anyway."""
    ctx = agent_context or {}
    base = Path(str(ctx.get("project_root") or _default_root())).expanduser().resolve()
    if not explicit:
        return str(base)
    given = Path(str(explicit)).expanduser()
    if not given.is_absolute():
        given = base / given
    given = given.resolve()
    # zg indexes the checkout as a whole; a sub-directory of it searches the
    # same index, so the call is made with the checkout root.
    try:
        given.relative_to(base)
        return str(base)
    except ValueError:
        return str(given)


def _hybrid_search(root: str, query: str, limit: int) -> Optional[str]:
    """Ask the zvec-grep MCP server; None when it is not there or refuses."""
    try:
        from backend.services.mcp_client_service import MCP_ENABLED, get_mcp_service, run_mcp_async
    except Exception as exc:  # noqa: BLE001 - the fallback covers it
        logger.debug("code search: MCP client unavailable: %s", exc)
        return None
    if not MCP_ENABLED:
        return None
    try:
        service = get_mcp_service()
        result = run_mcp_async(service.call_tool(
            MCP_SERVER, MCP_TOOL, {"root": root, "query": query, "limit": int(limit)},
        ))
    except Exception as exc:  # noqa: BLE001
        logger.info("code search: hybrid call failed, using regex search: %s", exc)
        return None
    if not result or not result.get("success"):
        return None
    payload = result.get("result") or {}
    content = payload.get("content") if isinstance(payload, dict) else None
    text = "\n".join(
        item.get("text", "") for item in (content or []) if isinstance(item, dict) and item.get("type") == "text"
    ).strip()
    if not text or (isinstance(payload, dict) and payload.get("isError")):
        logger.info("code search: hybrid returned an error, using regex search: %s", text[:200])
        return None
    return text


def _regex_search(query: str) -> str:
    """The repository's own regex search, widened word by word for prose queries."""
    from backend.tools.llama_code_tools import search_code
    out = search_code(re.escape(query))
    if "No matches" not in out and out.strip():
        return out
    words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", query)]
    if not words:
        return out
    return search_code("|".join(re.escape(w) for w in words[:6]))


class SearchCodebaseTool(BaseTool):
    name = "search_codebase"
    description = (
        "Search the current project's source code, which is already indexed: no path or "
        "upload is needed, just call it. Ask by meaning or by symbol: 'where is it decided "
        "whether a model supports thinking', 'which function cuts retrieved text', "
        "'callers of think_payload'. Returns the matching files with line numbers and the "
        "code itself. Use it before saying the code is unavailable, before reading whole "
        "files, and instead of shell commands like grep or ls."
    )
    category = "code"
    observation_chars = MAX_OUTPUT_CHARS
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=True,
            description="What to find, in plain words or as a symbol name",
        ),
        "limit": ToolParameter(
            name="limit", type="int", required=False, default=DEFAULT_LIMIT,
            description="How many hits to return (default 8)",
        ),
        "root": ToolParameter(
            name="root", type="string", required=False,
            description="Checkout to search; leave empty for the current project",
        ),
    }

    def execute(self, **kwargs: Any) -> ToolResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="query is required")
        try:
            limit = max(1, min(int(kwargs.get("limit") or DEFAULT_LIMIT), 25))
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        root = _resolve_root(kwargs.get("root"), kwargs.get("_agent_context"))
        if not Path(root).is_dir():
            return ToolResult(success=False, error=f"root is not a directory: {root}")

        text = _hybrid_search(root, query, limit)
        engine = "hybrid"
        if text is None:
            text = _regex_search(query)
            engine = "regex"
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS].rsplit("\n", 1)[0] + "\n... [more hits omitted]"
        return ToolResult(success=True, output=text, metadata={"engine": engine, "root": root, "limit": limit})
