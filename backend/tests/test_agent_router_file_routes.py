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
