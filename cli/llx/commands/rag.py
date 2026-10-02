"""RAG inspection — index stats, entity exploration, evaluation, and debug tracing."""

import typer
from rich.table import Table
from rich.tree import Tree

from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx.kb import list_documents, search_passages
from llx.theme import make_console, make_panel, ICON_SUCCESS, ICON_WARNING
from llx import output

console = make_console()
rag_app = typer.Typer(help="RAG index inspection and evaluation", no_args_is_help=True)


@rag_app.command("status")
def rag_status(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show RAG index statistics — document counts, index health, storage size."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        listing, meta = list_documents(client)
        try:
            info = client.get("/api/meta/index-info")
        except LlxError:
            info = {}
        docs = []
        for line in listing.splitlines()[1:]:
            name, sep, rest = line.strip().partition(" — ")
            if sep and "passages" in rest:
                docs.append({"document": name, "passages": rest.split(" passages")[0]})
        status_data = {
            "documents": meta.get("total"),
            "passages_listed": sum(int(d["passages"]) for d in docs if d["passages"].isdigit()),
            "embedding_model": info.get("embedding_model"),
            "documents_listed": docs,
        }

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": status_data})
            return

        total = status_data["documents"]
        lines = [
            f"[llx.kv.key]Documents indexed:[/llx.kv.key] {total if total is not None else '?'}",
            f"[llx.kv.key]Passages:[/llx.kv.key]          {status_data['passages_listed']}"
            + (" (first 200 documents)" if (total or 0) > len(docs) else ""),
            f"[llx.kv.key]Embedding model:[/llx.kv.key]   [llx.accent]{status_data['embedding_model'] or '?'}[/llx.accent]",
        ]
        console.print(make_panel("\n".join(lines), title="RAG Index"))
        if docs:
            output.print_table(docs[:10], columns=["document", "passages"],
                               title="Largest documents" if len(docs) > 10 else "Documents")

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)


@rag_app.command("query")
def rag_query(
    query: str = typer.Argument(help="Search query to test against the RAG index"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results to retrieve"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Test a RAG query and see retrieved chunks with relevance scores."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        results, trace = search_passages(client, query, top_k)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {"results": results, "retrieval": trace}})
            return

        if not results:
            console.print(f"[llx.dim]No results for: {query}[/llx.dim]")
            return

        console.print(f"[llx.accent]Results for:[/llx.accent] [bold]{query}[/bold]\n")

        for i, r in enumerate(results, 1):
            score = r["score"] if isinstance(r["score"], (int, float)) else 0.0
            source = r["source"] + (f" p.{r['page']}" if r["page"] else "")
            text = r["text"][:200]

            score_color = "llx.success" if score > 0.7 else ("llx.warning" if score > 0.4 else "llx.error")
            console.print(f"  [bold]{i}.[/bold] [{score_color}]{score:.3f}[/{score_color}]  [llx.accent]{source}[/llx.accent]")
            console.print(f"     [llx.dim]{text}{'...' if len(r['text']) > 200 else ''}[/llx.dim]")
            console.print()

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)


@rag_app.command("entities")
def rag_entities(
    limit: int = typer.Option(25, "--limit", "-l", help="Max entities to show"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """List extracted entities from the RAG knowledge graph."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        output.print_error(
            "Guaardvark does not build an entity graph from your documents, so there is nothing "
            "to list. `guaardvark rag status` shows what is indexed.", code="NOT_AVAILABLE")
        raise typer.Exit(1)

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)


@rag_app.command("eval")
def rag_eval(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show RAG autoresearch evaluation results and optimization status."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get("/api/autoresearch/status")
        status_data = data.get("data", data)

        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": status_data})
            return

        enabled = status_data.get("enabled", False)
        last_run = status_data.get("last_run", "Never")
        experiments = status_data.get("completed_experiments", 0)
        best_score = status_data.get("best_score")
        current_score = status_data.get("current_score")

        lines = []
        e_style = "llx.success" if enabled else "llx.dim"
        lines.append(f"[{e_style}]{ICON_SUCCESS if enabled else '  '} Autoresearch {'Enabled' if enabled else 'Disabled'}[/{e_style}]")
        lines.append(f"[llx.kv.key]Last Run:[/llx.kv.key]       {last_run}")
        lines.append(f"[llx.kv.key]Experiments:[/llx.kv.key]    {experiments}")

        if current_score is not None:
            score_color = "llx.success" if current_score > 0.7 else ("llx.warning" if current_score > 0.4 else "llx.error")
            lines.append(f"[llx.kv.key]Current Score:[/llx.kv.key]  [{score_color}]{current_score:.3f}[/{score_color}]")
        if best_score is not None:
            lines.append(f"[llx.kv.key]Best Score:[/llx.kv.key]     {best_score:.3f}")

        console.print(make_panel("\n".join(lines), title="RAG Autoresearch"))

    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(e.message, code="API_ERROR")
        raise typer.Exit(1)
