"""
Memory Tools — Allows the agent to manage its own long-term memory.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.models import db, AgentMemory, AgentMemoryAudit
from backend.api.memory_api import add_memory, _query_memories
from backend.services.memory_contract import query_terms
from backend.utils.backend_http import BackendError, is_mcp_transport, request_json, run_tool_in_backend

logger = logging.getLogger(__name__)

class SaveMemoryTool(BaseTool):
    """Save a fact, preference, or note to long-term memory."""
    
    name = "save_memory"
    read_only = False
    destructive = False
    description = (
        "Store one fact, preference or standing instruction in Guaardvark's long-term memory; returns "
        "'Successfully saved to long-term memory (ID: <id>).' Guaardvark's chat recalls relevant "
        "entries into its prompts: facts as ground truth, notes as rules, preferences as defaults. Use "
        "it when the user tells you about themselves, their projects or how they want you to behave; "
        "run search_memory first to avoid duplicates. Each call adds a new entry (nothing is merged); "
        "over MCP the entry is visible to every project and session. Entries are edited or deleted on "
        "the Agent Memory page, not with this tool. Needs the Guaardvark backend running."
    )
    is_dangerous = False
    requires_approval = False

    parameters = {
        "content": ToolParameter(
            name="content",
            type="string",
            description="The memory as one self-contained statement, e.g. 'Prefers metric units'. Guaardvark's chat shows at most 200 characters of a fact, 400 of a note and 250 of a preference.",
            required=True
        ),
        "type": ToolParameter(
            name="type",
            type="string",
            description="'fact' (default): something true about the user or their work, recalled as ground truth; 'preference': a default the user can override per request; 'note': a standing instruction ('instruction' is stored as 'note'). Unknown values are stored as 'note'.",
            required=False,
            default="fact"
        ),
        "tags": ToolParameter(
            name="tags",
            type="list",
            items="string",
            description="Keywords for the entry, e.g. ['python', 'formatting']; stored lower-cased without duplicates. search_memory matches tags as well as content.",
            required=False,
            default=[]
        ),
        "importance": ToolParameter(
            name="importance",
            type="float",
            minimum=0.0,
            maximum=1.0,
            description="0.0-1.0, default 0.8. The heaviest single factor when Guaardvark's chat ranks recall; the three highest facts or notes at 0.85 or above join every recall even when they do not match the question.",
            required=False,
            default=0.8
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        content = kwargs.get("content")
        mem_type = kwargs.get("type", "fact")
        tags = kwargs.get("tags", [])
        importance = kwargs.get("importance", 0.8)
        agent_context = kwargs.get("_agent_context", {})
        session_id = agent_context.get("session_id")
        project_id = agent_context.get("project_id")
        workspace_root = agent_context.get("workspace_root")

        if not (content or "").strip():
            return ToolResult(success=False, error="content is required.")

        if is_mcp_transport(self):
            return self._save_via_backend(
                content, mem_type, tags, importance, session_id, project_id, workspace_root,
            )

        try:
            memory = add_memory(
                content=content,
                memory_type=mem_type,
                source="agent",
                session_id=session_id,
                project_id=project_id,
                workspace_root=workspace_root,
                tags=tags,
                importance=importance,
            )
            if memory is None:
                return ToolResult(success=False, error="Memory was rejected or could not be saved.")

            logger.info(f"Agent saved memory {memory.id}: {content[:50]}...")
            return ToolResult(
                success=True,
                output=f"Successfully saved to long-term memory (ID: {memory.id}).",
                metadata={"id": memory.id, "content": memory.content, "type": memory.type}
            )
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error(f"Failed to save memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")

    def _save_via_backend(self, content, mem_type, tags, importance, session_id, project_id, workspace_root) -> ToolResult:
        # The MCP server process has no Flask app; POST /api/memory owns
        # validation, the audit row and the commit.
        payload = {
            "content": content,
            "type": mem_type,
            "source": "agent",
            "tags": tags,
            "importance": importance,
            "session_id": session_id,
            "project_id": project_id,
            "workspace_root": workspace_root,
        }
        try:
            body = request_json("POST", "/api/memory", payload=payload).body
        except BackendError as e:
            return ToolResult(success=False, error=f"Memory was not saved: {e}")
        memory = (body or {}).get("memory") or {}
        if not memory.get("id"):
            return ToolResult(success=False, error="Memory was not saved: the backend returned no memory id.")
        return ToolResult(
            success=True,
            output=f"Successfully saved to long-term memory (ID: {memory['id']}).",
            metadata={"id": memory["id"], "content": memory.get("content"), "type": memory.get("type")},
        )


class SearchMemoryTool(BaseTool):
    """Search the agent's long-term memory."""
    
    name = "search_memory"
    read_only = True
    description = (
        "Look up entries in Guaardvark's long-term memory: facts, preferences and notes saved with "
        "save_memory or in the app, and the entries Guaardvark keeps itself, which are lessons "
        "(returned as their stored JSON, a title and steps), lesson summaries, snippets and the "
        "screen agent's 'belief_update' observations. Every type is searched and each line shows "
        "its type. An entry matches when its content or tags contain, as a whole word, any of the "
        "query's first eight keywords: words of three or more characters, not counting common words "
        "such as 'what', 'is', 'my' or 'the' (case-insensitive text, not semantic); results are ranked by "
        "importance, match, source trust, confidence and recency. Returns lines "
        "'- [ID: <id>] (<type>) <content>', or 'No memories found matching ...'; a query with no "
        "keyword lists the top entries instead. Read-only: searching does not count as a recall. Use it "
        "to recall what the user said earlier and before "
        "save_memory; for indexed documents use search_knowledge_base, for the web web_search. Needs "
        "the Guaardvark backend running."
    )
    is_dangerous = False
    requires_approval = False
    
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="Keywords to look for, e.g. 'units python'. Only the first eight keywords count; words under three characters and common words ('what', 'is', 'my', 'the') are ignored, and a query with no keyword lists the top entries.",
            required=True
        ),
        "limit": ToolParameter(
            name="limit",
            type="integer",
            minimum=1,
            maximum=50,
            description="How many entries to return, 1-50 (default 5).",
            required=False,
            default=5
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        query = str(kwargs.get("query") or "").lower()
        try:
            limit = max(1, min(int(kwargs.get("limit") or 5), 50))
        except (TypeError, ValueError):
            limit = 5

        if is_mcp_transport(self):
            # The backend runs this same tool, so MCP and chat match the same way.
            return run_tool_in_backend(self.name, {"query": query, "limit": limit})

        # The same keywords _query_memories filters on; without one, list the top entries.
        searching = bool(query_terms(query))
        try:
            memories = _query_memories(
                query=query if searching else None, limit=limit, raise_errors=True,
                include_always_on=False, count_access=False,
            )

            if not memories:
                return ToolResult(
                    success=True,
                    output=f"No memories found matching '{query}'." if searching else "No memories saved yet.",
                    metadata={"results": []}
                )

            results = []
            output_lines = [
                f"Found {len(memories)} memories matching '{query}':" if searching
                else f"Top {len(memories)} memories:"
            ]
            for m in memories:
                results.append(m.to_dict())
                output_lines.append(f"- [ID: {m.id}] ({m.type}) {m.content}")
                
            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata={"results": results}
            )
        except Exception as e:
            logger.error(f"Failed to search memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")


class DeleteMemoryTool(BaseTool):
    """Delete a memory by ID."""
    
    name = "delete_memory"
    description = "Delete a specific memory by its ID. Use this if a user tells you to forget something."
    is_dangerous = True
    requires_approval = True
    
    parameters = {
        "memory_id": ToolParameter(
            name="memory_id",
            type="string",
            description="The ID of the memory to delete (obtained from search_memory).",
            required=True
        )
    }

    def execute(self, **kwargs) -> ToolResult:
        memory_id = kwargs.get("memory_id")
        
        try:
            memory = db.session.query(AgentMemory).filter_by(id=memory_id).first()
            if not memory:
                return ToolResult(
                    success=False,
                    error=f"Memory with ID '{memory_id}' not found."
                )
                
            content = memory.content
            before = memory.to_dict()
            db.session.delete(memory)
            db.session.add(AgentMemoryAudit(
                memory_id=memory_id,
                action="delete",
                actor="agent",
                before=before,
            ))
            db.session.commit()
            
            logger.info(f"Agent deleted memory {memory_id}")
            return ToolResult(
                success=True,
                output=f"Successfully deleted memory: '{content}'",
                metadata={"deleted_id": memory_id}
            )
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to delete memory: {e}")
            return ToolResult(success=False, error=f"Database error: {str(e)}")
