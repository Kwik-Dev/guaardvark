"""grade_draft_externally says whether a grade actually came back.

``checked`` is True only when the grader model answered with a grade; a missing
model or a failed call is unchecked (and still ``skipped``, for older readers).
The Ollama HTTP call and the model lookup are stand-ins.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.services.social_outreach import external_grader


def _answer(content):
    def post(url, json=None, timeout=None):
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"message": {"content": content}})
    return post


@pytest.fixture(autouse=True)
def _no_model_info():
    with patch("backend.utils.ollama_resource_manager.get_model_info", return_value=None):
        yield


def test_a_returned_grade_is_checked():
    reply = '{"grade": 0.75, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 0, "reason": "ok"}'
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is True
    assert result["skipped"] is False
    assert result["grade"] == pytest.approx(0.75)


def test_no_grader_model_is_unchecked():
    with patch.object(external_grader, "_resolve_grader_model", return_value=None):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"] == "no_grader_model_loaded"


def _refused(url, json=None, timeout=None):
    raise ConnectionError("refused")


@pytest.mark.parametrize("post", [_answer("not json at all"), _refused])
def test_a_failed_call_is_unchecked(post):
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", post):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"].startswith("grader_call_failed")
