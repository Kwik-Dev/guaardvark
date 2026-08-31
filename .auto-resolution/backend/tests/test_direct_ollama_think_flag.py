"""Direct Ollama calls outside the chat engine ask thinking models for an answer only.

Two representative sites: the Python client shape (``ollama.chat(**kw)``) and the
raw HTTP shape (a ``/api/chat`` JSON body). Both take the flag from
``think_payload`` so the gate lives in one place.
"""
from types import SimpleNamespace
from unittest.mock import patch

from backend.services import nl_control_plane
from backend.services.social_outreach import external_grader


def test_python_client_site_sends_think_false_for_a_thinking_model():
    calls = []

    def chat(**kw):
        calls.append(kw)
        return {"message": {"content": '{"intent": "status"}'}}

    with patch("ollama.chat", chat), \
            patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        out = nl_control_plane.json_chat("sys", "usr", model="gemma4:e2b")
    assert out == {"intent": "status"}
    assert calls[0]["think"] is False
    assert calls[0]["format"] == "json"


def test_python_client_site_sends_nothing_for_other_models():
    calls = []

    def chat(**kw):
        calls.append(kw)
        return {"message": {"content": "{}"}}

    with patch("ollama.chat", chat), \
            patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        nl_control_plane.json_chat("sys", "usr", model="llama3:latest")
    assert "think" not in calls[0]


def test_raw_http_site_puts_think_false_in_the_body():
    posted = []

    def post(url, json=None, timeout=None):
        posted.append(json)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"message": {"content": '{"grade": 0.8}'}},
        )

    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", post), \
            patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        external_grader.grade_draft_externally("draft", "thread")
    assert posted[0]["think"] is False
    assert posted[0]["model"] == "gemma4:e2b"
