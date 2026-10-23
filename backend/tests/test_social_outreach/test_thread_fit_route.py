"""POST /thread-fit puts a message to the thread-fit judge for callers outside
the backend (the Discord cog) and drafts nothing.

The judge (external_grader.score_thread_relevance) is a stand-in; the route,
the profile feature match and recon.judged_unfit run for real.
"""

from __future__ import annotations

import pytest

from backend.api import social_outreach_api
from backend.models import SocialOutreachLog
from backend.services.social_outreach import external_grader, persona


@pytest.fixture
def route(app, client, monkeypatch, outreach_profile):
    app.register_blueprint(social_outreach_api.social_outreach_bp)
    state = {"answer": {"grade": 0.9, "verdict": "good_fit", "reason": "r", "skipped": False,
                        "model": "g"},
             "asked": []}

    def judge(**kwargs):
        state["asked"].append(kwargs)
        return dict(state["answer"])

    monkeypatch.setattr(external_grader, "score_thread_relevance", judge)
    drafted = []
    monkeypatch.setattr(persona, "draft_outreach_text", lambda **kw: drafted.append(kw) or {})

    def call(**body):
        return client.post("/api/social-outreach/thread-fit", json=body)

    call.state = state
    call.drafted = drafted
    return call


def test_text_is_required(route):
    assert route(title="t").status_code == 400
    assert route.state["asked"] == []


@pytest.mark.parametrize("answer, unfit", [
    ({"grade": 0.9, "verdict": "good_fit", "skipped": False}, False),
    ({"grade": 0.1, "verdict": "skip", "skipped": False}, True),
    ({"grade": 0.9, "verdict": "skip", "skipped": False}, True),
    ({"grade": 0.3, "verdict": "good_fit", "skipped": False}, True),
    ({"grade": 0.0, "skipped": True, "reason": "no_grader_model_loaded"}, False),
])
def test_unfit_is_the_judges_answer_as_recon_reads_it(route, answer, unfit):
    route.state["answer"] = answer

    body = route(text="my voice is gone today, no stream tonight").get_json()

    assert body["unfit"] is unfit
    assert body["skipped"] is answer["skipped"]


def test_the_judge_gets_the_message_and_the_last_three_earlier_ones(route):
    route(text="Which GPU do I need?", title="Discord message in #help",
          context=["one", "two", "three", "four"], feature_hint="ollama")

    asked = route.state["asked"][0]
    assert asked["selftext"] == "Which GPU do I need?"
    assert asked["title"] == "Discord message in #help"
    assert asked["top_comments"] == ["two", "three", "four"]


def test_a_profile_feature_in_the_text_is_the_hint(route):
    body = route(text="Is retrieval any good with a 12GB card?", feature_hint="voice").get_json()

    assert route.state["asked"][0]["feature_hint"] == "rag"
    assert body["feature_hint"] == "rag"


def test_the_callers_keyword_is_the_hint_when_no_profile_feature_matches(route):
    body = route(text="my voice is gone today, no stream tonight", feature_hint="voice").get_json()

    assert body["feature_hint"] == "voice"


def test_nothing_is_drafted_or_recorded(route):
    route(text="Which GPU do I need to run ollama?")

    assert route.drafted == []
    assert SocialOutreachLog.query.count() == 0
