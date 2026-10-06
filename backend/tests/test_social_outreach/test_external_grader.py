"""grade_draft_externally says whether the grader's answers actually came back.

``checked`` is True only when the grader model answered its questions; a missing
model or a failed call is unchecked (and still ``skipped``, for older readers).
The Ollama HTTP call and the model lookup are stand-ins.
"""

from __future__ import annotations

import json
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


def test_a_returned_answer_is_checked_and_graded_in_code():
    """The model's own grade (0.75) and concise (0) are not read: a one-word
    draft is concise, so all four items hold and the grade is 1.0."""
    reply = '{"grade": 0.75, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 0, "reason": "ok"}'
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is True
    assert result["skipped"] is False
    assert result["passed"] is True
    assert result["concise"] == 1
    assert result["grade"] == pytest.approx(1.0)


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


# ---- rubric items are read as booleans --------------------------------------------------

def _grade_with_engages(engages_json):
    reply = ('{"grade": 0.75, "engages": %s, "on_topic": 1, "appropriate_tone": 1, '
             '"concise": 1, "reason": "ok"}' % engages_json)
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        return external_grader.grade_draft_externally("draft", "thread")


@pytest.mark.parametrize("engages_json, bit", [
    ('"yes"', 1), ('"true"', 1), ("1", 1), ('"1"', 1), ("true", 1),
    ('"no"', 0), ('"false"', 0), ("0", 0), ('"0"', 0), ("false", 0),
])
def test_a_rubric_item_reads_true_yes_1_and_false_no_0(engages_json, bit):
    result = _grade_with_engages(engages_json)

    assert result["checked"] is True
    assert result["engages"] == bit


@pytest.mark.parametrize("engages_json", ['"maybe"', "2", "null", "[1]", '"sometimes"'])
def test_an_odd_rubric_answer_leaves_the_draft_unchecked(engages_json):
    result = _grade_with_engages(engages_json)

    assert result["checked"] is False
    assert result["skipped"] is True
    assert result["reason"] == "grader_reply_unparsed"


def test_a_missing_rubric_item_leaves_the_draft_unchecked():
    reply = '{"grade": 0.75, "on_topic": 1, "appropriate_tone": 1, "concise": 1, "reason": "ok"}'
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        result = external_grader.grade_draft_externally("draft", "thread")

    assert result["checked"] is False
    assert result["reason"] == "grader_reply_unparsed"


# ---- passing takes all three questions; the model's own total does not count -----------

def _grade(reply, draft="draft"):
    with patch.object(external_grader, "_resolve_grader_model", return_value="gemma4:e2b"), \
            patch.object(external_grader.requests, "post", _answer(reply)):
        return external_grader.grade_draft_externally(draft, "thread")


def test_a_high_model_grade_does_not_pass_a_draft_that_does_not_engage():
    result = _grade('{"grade": 0.75, "engages": 0, "on_topic": 0, "appropriate_tone": 1, '
                    '"concise": 1, "reason": "generic"}')

    assert result["checked"] is True
    assert result["passed"] is False
    assert result["grade"] == pytest.approx(0.5)


@pytest.mark.parametrize("failing", ["engages", "on_topic", "appropriate_tone"])
def test_any_one_question_failing_fails_the_draft(failing):
    answers = {"engages": 1, "on_topic": 1, "appropriate_tone": 1, failing: 0}
    reply = ('{"grade": 1.0, "engages": %(engages)d, "on_topic": %(on_topic)d, '
             '"appropriate_tone": %(appropriate_tone)d, "concise": 1, "reason": "r"}' % answers)

    result = _grade(reply)

    assert result["passed"] is False
    assert result["grade"] == pytest.approx(0.75)


@pytest.mark.parametrize("words, concise", [(119, 1), (120, 1), (121, 0)])
def test_concise_is_counted_from_the_draft(words, concise):
    """The model says concise for every length here; only the word count decides."""
    reply = '{"grade": 1.0, "engages": 1, "on_topic": 1, "appropriate_tone": 1, "concise": 1, "reason": "r"}'

    result = _grade(reply, draft=" ".join(["word"] * words))

    assert result["concise"] == concise
    assert result["grade"] == pytest.approx((3 + concise) / 4)
    assert result["passed"] is True


# ---- the prompt asks only for what is read ----------------------------------------------

def test_the_prompt_asks_only_for_the_three_answers():
    prompt = external_grader.GRADER_SYSTEM
    for question in external_grader.RUBRIC_QUESTIONS:
        assert f"{question}:" in prompt
    assert "concise" not in prompt
    assert "grade" not in prompt.replace("grader", "")
    example = json.loads(prompt[prompt.index("{"):prompt.rindex("}") + 1])
    assert set(example) == {*external_grader.RUBRIC_QUESTIONS, "reason"}


def test_a_reply_in_the_asked_shape_is_checked():
    result = _grade('{"engages": 1, "on_topic": 1, "appropriate_tone": 1, "reason": "fits"}')

    assert result["checked"] is True
    assert result["passed"] is True
    assert result["grade"] == pytest.approx(1.0)
