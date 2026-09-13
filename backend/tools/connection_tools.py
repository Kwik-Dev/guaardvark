"""Chat and MCP handle on the Connections publish queue.

An agent can ask for a post; it cannot send one. Each request is recorded with
the surface that made it, and ``gates.requires_approval`` holds chat, MCP and
unattributed requests at ``awaiting_approval`` until a person approves them on
the Approvals page, whatever the publish settings say.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.utils.backend_http import BackendError, is_mcp_transport, request_json

logger = logging.getLogger(__name__)

SOCIAL_FAMILY = "social"
AGENT_TRANSPORTS = ("chat", "mcp")


def _request_source(tool: BaseTool) -> str:
    """The surface that called the tool. Never taken from tool arguments, so a
    caller cannot claim to be the UI; anything unrecognised stays supervised."""
    context = getattr(tool, "_context", None) or {}
    transport = str(context.get("transport") or "").strip().lower()
    return transport if transport in AGENT_TRANSPORTS else "unknown"


def _list_local() -> List[Dict[str, Any]]:
    from backend.services.connections import service

    return service.list_connections(SOCIAL_FAMILY)


def _queue_local(**kwargs) -> Dict[str, Any]:
    from backend.services.connections import publish_service

    return publish_service.queue_publish(**kwargs)


def _label(connection: Dict[str, Any]) -> str:
    name = connection.get("display_name") or connection.get("provider")
    return f"{name} ({connection.get('provider')}, id {connection.get('id')})"


def _pick_connection(
    connections: List[Dict[str, Any]], wanted: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    usable = [c for c in connections if c.get("enabled")]
    if not usable:
        return None, "No social connection is set up. Add one on the Connections page first."
    choices = "; ".join(_label(c) for c in usable)
    if wanted is None or not str(wanted).strip():
        if len(usable) == 1:
            return usable[0], None
        return None, f"Say which connection to post to: {choices}."

    key = str(wanted).strip().lower()
    matches = [
        c for c in usable
        if key == str(c.get("id"))
        or key == (c.get("display_name") or "").strip().lower()
        or key == (c.get("provider") or "").lower()
        or key == (c.get("handle") or "").strip().lower()
    ]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"No enabled social connection matches '{wanted}'. Available: {choices}."
    return None, f"'{wanted}' matches more than one connection; pass its id. Available: {choices}."


class RequestPublishTool(BaseTool):
    """Queue a post to a social connection for a person to approve."""

    name = "request_publish"
    read_only = False
    destructive = False
    # The approval happens on the Approvals page, which gates.requires_approval
    # enforces for every chat and MCP request. Marking the tool itself as
    # approval-required would only hide it from MCP clients and ask twice in chat.
    requires_approval = False
    description = (
        "Ask to publish a text post to one of the user's social connections "
        "(Discord webhook, Bluesky, Mastodon, Telegram). This does NOT post: the "
        "request waits on the Approvals page until a person approves, rejects or "
        "cancels it, whatever the publish settings say. Omit connection when only "
        "one is set up; otherwise pass its id or name."
    )
    parameters = {
        "body": ToolParameter(
            name="body", type="string", required=True,
            description="The text of the post.",
        ),
        "connection": ToolParameter(
            name="connection", type="string", required=False,
            description="Connection id, display name, provider or handle. Optional when only one exists.",
        ),
        "title": ToolParameter(
            name="title", type="string", required=False,
            description="Title, for platforms that show one.",
        ),
        "link_url": ToolParameter(
            name="link_url", type="string", required=False,
            description="A link to attach to the post.",
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        body = (kwargs.get("body") or "").strip()
        if not body:
            return ToolResult(success=False, error="body is required: the text of the post.")
        source = _request_source(self)
        remote = is_mcp_transport(self)

        try:
            if remote:
                listed = request_json(
                    "GET", "/api/connections", params={"family": SOCIAL_FAMILY}
                ).data or {}
                connections = listed.get("connections", []) if isinstance(listed, dict) else []
            else:
                connections = _list_local()
        except BackendError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:  # noqa: BLE001 - surface a failed lookup to the agent
            logger.exception("request_publish: listing connections failed")
            return ToolResult(success=False, error=f"Could not list connections: {e}")

        connection, problem = _pick_connection(connections, kwargs.get("connection"))
        if problem:
            return ToolResult(success=False, error=problem)

        request = {
            "connection_ids": [connection["id"]],
            "body": body,
            "title": kwargs.get("title") or None,
            "link_url": kwargs.get("link_url") or None,
            "requested_by": source,
        }
        try:
            if remote:
                result = request_json("POST", "/api/connections/publish", payload=request).data or {}
            else:
                result = _queue_local(**request)
        except BackendError as e:
            return ToolResult(success=False, error=str(e))
        except (ValueError, RuntimeError) as e:
            return ToolResult(success=False, error=str(e))

        queued = result.get("queued", []) if isinstance(result, dict) else []
        record_ids = [q.get("publish_record_id") for q in queued if q.get("publish_record_id")]
        statuses = sorted({q.get("status") for q in queued if q.get("status")})
        waiting = bool(result.get("requires_approval")) if isinstance(result, dict) else False
        output = {
            "connection": _label(connection),
            "publish_record_ids": record_ids,
            "status": ", ".join(statuses) or "unknown",
            "requested_by": source,
            "next": (
                "Waiting on the Approvals page. Nothing is sent until a person approves it."
                if waiting else
                "Queued without an approval step; check the Jobs page for its progress."
            ),
        }
        return ToolResult(success=True, output=output,
                          metadata={"publish_record_ids": record_ids, "requires_approval": waiting})


__all__ = ["RequestPublishTool"]
