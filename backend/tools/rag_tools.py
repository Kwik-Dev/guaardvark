import logging
from typing import Any, Dict, List

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.indexing_service import search_with_llamaindex
from backend.utils.backend_http import is_mcp_transport, run_tool_in_backend

logger = logging.getLogger(__name__)

# Per-chunk cap for the rendered block. Chunks are quoted verbatim (never
# LLM-summarized) so the caller can cite them; this only bounds how much of a
# long chunk is shown before an explicit truncation marker.
_CHUNK_CHARS = 600


def _render(query: str, results: List[Dict[str, Any]], trace: Dict[str, Any]) -> str:
    """Render results as a compact, citable text block.

    The provenance banner comes first on purpose: consumers truncate tool output
    (the chat engine at 2000 chars), and a degradation notice that gets cut off
    is worse than none at all.
    """
    legs = "+".join(trace.get("legs") or []) or "none"
    fusion = trace.get("fusion") or "n/a"
    alpha = trace.get("eff_alpha")
    model = trace.get("embedding_model") or "unknown"
    dims = trace.get("embedding_dims")

    head = [f'KNOWLEDGE BASE — {len(results)} result(s) for "{query}"']
    detail = f"retrieval: {legs} ({fusion}"
    if alpha is not None:
        detail += f", alpha={alpha:.2f}"
    detail += f") · embed: {model}"
    if dims:
        detail += f" ({dims}d)"
    rr = trace.get("rerank") or {}
    if rr.get("applied"):
        detail += f" · rerank: {rr.get('model')} on {rr.get('device')}"
    elif rr.get("reason"):
        detail += f" · rerank: OFF ({rr['reason']})"
    if trace.get("mmr_applied"):
        detail += " · MMR"
    if trace.get("filters_applied"):
        detail += f" · filters={trace.get('filters')}"
    head.append(detail)

    # A project-scoped question answered from outside that project is the one
    # result a caller must not mistake for a normal one: the passages may belong
    # to a different client. Say so before the passages, not in a trace nobody
    # reads.
    if trace.get("project_scope") == "global_fallback":
        head.append(
            f"!! OUT OF SCOPE — no matches in project "
            f"{trace.get('fallback_from_project')}; these passages come from "
            f"elsewhere in the index and may belong to another client"
        )
    if trace.get("degraded"):
        head.append(f"!! DEGRADED — {trace.get('degraded_reason')}")
    if trace.get("error"):
        head.append(f"!! ERROR — {trace['error']}")

    if not results:
        head.append("")
        head.append("No matching content. The index may be empty, or the filters too narrow.")
        # Names of projects, clients and the like are never indexed text; a
        # search for one comes back empty here even when the record exists.
        head.append(
            f"If \"{query}\" is the name of something in Guaardvark (a project, client, "
            f"document, video, note, Cast member...), find_records with name=\"{query}\" checks "
            "those directly; find_files checks file names on disk."
        )
        return "\n".join(head)

    body = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata") or {}
        src = meta.get("source_filename") or meta.get("file_path") or "unknown source"
        page = meta.get("page_label")
        loc = f" p.{page}" if page else ""
        # Two numbers with different meanings. When the cross-encoder ran, its
        # relevance score is what the ranking starts from; the retrieval (fusion)
        # score is from the step before and does not follow the order shown.
        score = r.get("score")
        rerank = r.get("rerank_score")
        shown = []
        if isinstance(rerank, (int, float)):
            shown.append(f"rerank {rerank:.3f}")
        if isinstance(score, (int, float)):
            shown.append(f"retrieval {score:.3f}" if shown else f"score {score:.3f}")
        score_s = f" ({' · '.join(shown)})" if shown else ""
        text = (r.get("text") or "").strip()
        if len(text) > _CHUNK_CHARS:
            text = text[:_CHUNK_CHARS].rstrip() + f"… [+{len(r['text']) - _CHUNK_CHARS} chars]"
        body.append(f"\n[{i}] {src}{loc}{score_s}\n{text}")

    return "\n".join(head) + "\n" + "\n".join(body)


class KnowledgeSearchTool(BaseTool):
    """
    Tool for searching the internal knowledge base (RAG).
    Use this to retrieve information about the codebase, architecture, specific repositories,
    or any documents that have been indexed.
    """

    name = "search_knowledge_base"
    read_only = True
    # The passages are the answer: at the 500 default the model read the
    # header and the start of one passage.
    observation_chars = 4000
    description = (
        "Search the user's indexed documents and code repositories (the local knowledge base) by "
        "meaning and keywords. Returns the top passages (the configured number, 3 on a stock install, "
        "up to 50 via top_k), each with its filename, page when known and its scores: 'rerank' (the "
        "reranker's relevance score, which the ranking starts from, shown when it ran) and "
        "'retrieval' (the earlier search score, which does not follow the order shown); passages "
        "usually open with an index label, e.g. 'Document: <file>. Section: <path>.' for documents or "
        "'[python] File: <path>.' for code, before the source text. Once corpus "
        "summaries have been built, results can include LLM-written summaries named "
        "'[corpus summary ...]': cite only real filenames. Results are ranked, not filtered, so an "
        "off-topic question still gets the nearest passages; a header line flags degraded retrieval. "
        "Needs the Guaardvark backend running. For Guaardvark's own source use search_codebase; for "
        "saved facts, search_memory; to read a whole section, read_document_section."
    )
    parameters = {
        "query": ToolParameter(
            name="query",
            type="string",
            description="What to find, as a question or key terms in plain words; matched by meaning and, when keyword search is on, by exact terms too.",
            required=True
        ),
        "top_k": ToolParameter(
            name="top_k",
            type="int",
            minimum=1,
            maximum=50,
            description="How many passages to return, 1-50. Omit for the configured default (3 on a stock install).",
            required=False
        ),
        "filter_type": ToolParameter(
            name="filter_type",
            type="string",
            description=(
                "Keep only passages with this content_type label. Labels are set per passage by a "
                "heuristic, e.g. 'text', 'header', 'table', 'list' or 'code' for files, a language "
                "name such as 'python' for code, and 'repository_summary' or 'repository_map' for "
                "analysed repositories. One label can miss relevant passages; omit it to search "
                "everything."
            ),
            required=False
        ),
        "project_id": ToolParameter(
            name="project_id",
            type="string",
            description="Id of a Guaardvark project, e.g. '12', to search only its documents. Pass one only when the user gives it; no tool here lists projects.",
            required=False
        )
    }

    def __init__(self):
        super().__init__()

    def execute(self, query: str, top_k: int = None, filter_type: str = None,
                project_id: str = None) -> ToolResult:
        if is_mcp_transport(self):
            arguments = {"query": query, "top_k": top_k,
                         "filter_type": filter_type, "project_id": project_id}
            return run_tool_in_backend(
                self.name, {key: value for key, value in arguments.items() if value is not None}
            )
        logger.info(f"Executing KnowledgeSearchTool: {query}")
        try:
            # content_type is the type key actually stamped on every indexed node
            # (indexing/chunking), and it is what repository_analysis_service writes
            # for repo summaries. A bare "type" key exists only on those summaries.
            filters = {"content_type": filter_type} if filter_type else None
            payload = search_with_llamaindex(
                query,
                max_chunks=top_k,
                project_id=project_id,
                filters=filters,
                with_trace=True,
            )
            results = payload["results"]
            trace = payload["trace"]

            # An empty result set is a real answer, not a failure -- reporting it as
            # an error made "nothing indexed matches" indistinguishable from "the
            # index is broken", and the trace already carries which one it was.
            return ToolResult(
                success=True,
                output=_render(query, results, trace),
                metadata={"retrieval": trace, "results": results},
            )
        except Exception as e:
            logger.error(f"Error in KnowledgeSearchTool: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Failed to search knowledge base: {str(e)}"
            )
