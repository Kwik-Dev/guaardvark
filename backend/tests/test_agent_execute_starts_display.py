"""POST /api/agent-control/execute brings the agent display up first.

The display starts on demand and is stopped by stop.sh, so after a restart a
screen task used to fail at its first capture. The route now starts it, as the
chat tool does, and refuses with 503 when it cannot.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"


@pytest.fixture
def client():
    from backend.api.agent_control_api import agent_control_bp
    app = Flask(__name__)
    app.register_blueprint(agent_control_bp)
    return app.test_client()


def _post(client, display_up):
    service = MagicMock(is_active=False)
    with patch("backend.utils.agent_display_utils.start_agent_display_if_needed",
               return_value=display_up) as start, \
            patch("backend.services.agent_control_service.get_agent_control_service", return_value=service), \
            patch("backend.services.local_screen_backend.LocalScreenBackend"), \
            patch("threading.Thread") as thread:
        resp = client.post("/api/agent-control/execute", json={"task": "open firefox"})
    return resp, start, thread


def test_execute_starts_the_display_before_the_task(client):
    resp, start, thread = _post(client, display_up=True)
    assert resp.status_code == 200
    start.assert_called_once()
    thread.return_value.start.assert_called_once()


def test_execute_refuses_when_the_display_cannot_start(client):
    resp, start, thread = _post(client, display_up=False)
    assert resp.status_code == 503
    assert "display" in resp.get_json()["error"].lower()
    thread.assert_not_called()
