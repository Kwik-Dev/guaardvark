"""GET /api/tools lists the approval gate; /categories uses the registry's map.

Drives the real tools blueprint through Flask's test client with a stand-in
registry; no backend, GPU, network or database.
"""

from types import SimpleNamespace

import pytest
from flask import Flask

from backend.api.tools_api import tools_bp
from backend.services.agent_tools import ToolParameter
from backend.tools import tool_registry_init


def _tool(name, requires_approval=False, params=None):
    return SimpleNamespace(
        name=name,
        description=f"{name} description",
        requires_approval=requires_approval,
        parameters=params or {},
    )


TOOLS = {
    "media_status": _tool("media_status"),
    "system_command": _tool(
        "system_command", requires_approval=True,
        params={"command": ToolParameter(name="command", type="string", required=True,
                                         description="shell line")},
    ),
    "generate_csv": _tool("generate_csv"),
    "oddly_named": _tool("oddly_named"),
}


@pytest.fixture
def client(monkeypatch):
    # What the registry recorded at registration; oddly_named has no entry.
    monkeypatch.setattr(tool_registry_init, "_tool_categories", {
        "media_status": "media",
        "system_command": "system",
        "generate_csv": "generation",
    })
    app = Flask(__name__)
    app.register_blueprint(tools_bp)
    app.tool_registry = SimpleNamespace(
        list_tools=lambda: list(TOOLS),
        get_tool=TOOLS.get,
    )
    return app.test_client()


def test_list_carries_the_approval_gate_per_tool(client):
    body = client.get("/api/tools").get_json()
    assert body["success"] and body["count"] == 4
    gate = {t["name"]: t["requires_approval"] for t in body["tools"]}
    assert gate == {"media_status": False, "system_command": True,
                    "generate_csv": False, "oddly_named": False}
    gated = next(t for t in body["tools"] if t["name"] == "system_command")
    assert gated["parameters"]["command"]["required"] is True


def test_categories_come_from_the_registry_with_a_name_guess_only_as_fallback(client):
    body = client.get("/api/tools/categories").get_json()
    assert body["success"]
    assert body["categories"] == {
        "media": ["media_status"],
        "system": ["system_command"],
        "generation": ["generate_csv"],
        "other": ["oddly_named"],
    }
