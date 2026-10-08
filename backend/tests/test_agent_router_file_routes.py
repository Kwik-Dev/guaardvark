"""File and CSV requests route to the tool that makes that kind of file.

"create a CSV file ..." matches both the CSV pattern and the generic file
pattern; the CSV one has to win, or the chat proposes a .js filename.
"""
import pytest

from backend.services.agent_router import AgentRouter


@pytest.mark.parametrize("message, tool", [
    ("create a CSV file with three fruits and their colors", "generate_csv"),
    ("generate a csv of 10 cities", "generate_csv"),
    ("create 50 rows csv of products", "generate_bulk_csv"),
    ("create a file called notes.md with my todo list", "generate_file"),
    ("write the summary to report.txt", "generate_file"),
])
def test_file_requests_pick_the_matching_tool(message, tool):
    assert AgentRouter().route(message).tool_name == tool


@pytest.mark.parametrize("message, params", [
    ("create a python file that prints hello", {"extension": "py"}),
    ("create a file in python that prints hello", {"extension": "py"}),
    ("create a bash script file that backs up my photos", {"extension": "sh"}),
    ("create a file called notes.md with my todo list", {"filename": "notes.md", "extension": "md"}),
    ("Create a file called README.md", {"filename": "README.md", "extension": "md"}),
    ("write the summary to report.txt", {"filename": "report.txt", "extension": "txt"}),
    ("create a file that explains python decorators", None),
    ("create a file summarizing https://example.com/page.html", None),
    ("create a file with the version 1.5 notes", None),
])
def test_a_file_route_carries_the_name_or_language_asked_for(message, params):
    decision = AgentRouter().route(message)
    assert decision.tool_name == "generate_file"
    assert decision.tool_params == params


def test_the_file_generation_result_names_the_extension():
    router = AgentRouter()
    message = "create a python file that prints hello"
    out = router._handle_file_generation(router.route(message), message, {})
    assert out["suggested_filename"] is None
    assert out["extension"] == "py"

    message = "create a file called notes.md"
    out = router._handle_file_generation(router.route(message), message, {})
    assert out["suggested_filename"] == "notes.md"
