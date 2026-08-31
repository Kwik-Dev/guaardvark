from flask import Blueprint, request

from backend.utils.response_utils import success_response, error_response

command_bp = Blueprint("command_api", __name__, url_prefix="/api/command")


@command_bp.route("/analyze", methods=["POST"])
def analyze_command():
    """/analyze chat command — NOT YET IMPLEMENTED.

    Tracked objective (MASTER_TASKS): wire this to the real code-intelligence
    analyze flow (backend/api/code_intelligence_api.analyze_code) once the
    chat-command contract is defined (what gets analyzed: pasted code? current
    file? repo folder?). It previously echoed the request body back as
    "executed" — a success that did no work. Until implemented, report honestly.
    """
    return error_response(
        "The /analyze command is not implemented yet.",
        status_code=501,
        error_code="NOT_IMPLEMENTED",
    )


@command_bp.route("/codefile", methods=["POST"])
def codefile_command():
    """/codefile chat command — NOT YET IMPLEMENTED.

    Tracked objective (MASTER_TASKS): wire to the real code read/write path once
    the contract is defined. Previously echoed the body back as "executed".
    """
    return error_response(
        "The /codefile command is not implemented yet.",
        status_code=501,
        error_code="NOT_IMPLEMENTED",
    )


@command_bp.route("/websearch", methods=["POST"])
def websearch_command():
    """Execute explicit web search command."""
    import logging
    logger = logging.getLogger(__name__)

    try:
        data = request.get_json() or {}
        query = data.get("query", data.get("message", ""))

        if not query:
            return error_response(
                "Please provide a search query.",
                status_code=400,
                error_code="NO_QUERY",
            )

        logger.info(f"/websearch command: '{query}'")

        # Import web search functionality
        from backend.api.web_search_api import enhanced_web_search

        # Perform search
        search_results = enhanced_web_search(query)

        if search_results.get("success"):
            result_data = search_results.get("data", {})
            result_type = result_data.get("type", "unknown")

            # Format response based on result type
            if result_type == "weather":
                response_text = (
                    f"Current weather in {result_data['location']}:\n"
                    f"Temperature: {result_data['temperature_fahrenheit']}°F "
                    f"({result_data['temperature_celsius']}°C)\n"
                    f"Conditions: {result_data['description']}\n"
                    f"Humidity: {result_data['humidity']}%"
                )
            elif result_type == "search_results":
                results = result_data.get("results", [])[:5]
                snippets = []
                for i, result in enumerate(results, 1):
                    snippets.append(
                        f"{i}. {result.get('title', 'N/A')}\n"
                        f"   {result.get('snippet', 'No description')}\n"
                        f"   URL: {result.get('url', 'N/A')}"
                    )
                response_text = "Search results:\n\n" + "\n\n".join(snippets)
            else:
                response_text = result_data.get("snippet", str(result_data))

            return success_response(
                data={
                    "query": query,
                    "response": response_text,
                    "raw_results": search_results,
                    "result_type": result_type
                },
                message="Web search completed successfully"
            )
        else:
            error = search_results.get("error", "Unknown error")
            return error_response(
                f"Web search failed: {error}",
                status_code=502,
                error_code="WEB_SEARCH_FAILED",
                data={"query": query, "error": error},
            )

    except Exception as e:
        logger.error(f"/websearch command error: {e}", exc_info=True)
        return error_response(
            f"Web search error: {str(e)}",
            status_code=500,
            error_code="WEB_SEARCH_ERROR",
            data={"error": str(e)},
        )
