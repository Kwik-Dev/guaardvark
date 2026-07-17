"""Natural-language outreach intent: classify then dispatch (no placebo queue)."""
from unittest.mock import MagicMock, patch


def _classifier(payload: dict):
    """Return a classifier fn that ignores prompts and returns payload."""

    def _fn(system, user):
        return dict(payload)

    return _fn


def test_status_question_never_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "status",
        "platform": "youtube",
        "topics": [],
        "draft_id": None,
        "confidence": 0.95,
        "reason": "user asked for status",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.kill_switch.is_supervised", return_value=True), \
         patch("backend.services.social_outreach.intent.kill_switch.cadence_status", return_value={}), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        result = execute_outreach_intent(
            "what is the status of the youtube outreach item?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert result["intent"] == "status"
        assert result.get("task_ids") == []
        assert result.get("refused") is False
        mock_q.assert_not_called()


def test_nonsense_refuses_never_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "refuse",
        "platform": None,
        "topics": [],
        "draft_id": None,
        "confidence": 0.99,
        "reason": "off-topic nursery rhyme",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = execute_outreach_intent(
            "did mary have a little lamb?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert result["refused"] is True
        assert result.get("task_ids") == []
        mock_q.assert_not_called()


def test_scout_youtube_offline_ai_comfyui_queues(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["Offline AI", "ComfyUI"],
        "draft_id": None,
        "confidence": 0.92,
        "reason": "user asked to comment on youtube about those topics",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.kill_switch.is_supervised", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        mock_q.return_value = {
            "task_id": 42,
            "job_id": "task_42",
            "message": "queued",
        }
        result = execute_outreach_intent(
            "comment on some youtube videos regarding Offline AI or ComfyUI",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is True
        assert 42 in result["task_ids"]
        mock_q.assert_called_once()
        kwargs = mock_q.call_args
        assert kwargs[0][0] == "youtube"
        assert "Offline AI" in kwargs[1]["keyword_profiles"]
        assert "ComfyUI" in kwargs[1]["keyword_profiles"]
        assert kwargs[1]["chain_draft"] is True


def test_low_confidence_scout_refuses(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["maybe AI?"],
        "draft_id": None,
        "confidence": 0.4,
        "reason": "uncertain",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:
        result = execute_outreach_intent(
            "maybe do something with youtube?",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert result["refused"] is True
        mock_q.assert_not_called()


def test_structured_platform_topics_skips_classifier(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=True), \
         patch("backend.services.social_outreach.intent.kill_switch.is_supervised", return_value=True), \
         patch("backend.services.social_outreach.intent.queue_outreach_run") as mock_q:

        mock_q.return_value = {"task_id": 7, "job_id": "task_7"}
        # classifier would refuse if called — ensure we never call it
        bad_clf = MagicMock(side_effect=AssertionError("classifier should not run"))
        result = execute_outreach_intent(
            "",
            platform="youtube",
            topics=["Offline AI", "ComfyUI"],
            action="comment",
            created_by="test",
            classifier=bad_clf,
        )
        assert result["ok"] is True
        mock_q.assert_called_once()
        bad_clf.assert_not_called()


def test_execute_intent_kill_switch_off(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    clf = _classifier({
        "intent": "scout_and_draft",
        "platform": "youtube",
        "topics": ["ollama"],
        "draft_id": None,
        "confidence": 0.9,
        "reason": "scout",
    })

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=False):
        result = execute_outreach_intent(
            "scout youtube about ollama",
            created_by="test",
            classifier=clf,
        )
        assert result["ok"] is False
        assert "disabled" in (result.get("error") or "").lower()


def test_intent_api_endpoint(app, client):
    from backend.api import social_outreach_api

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with patch(
        "backend.services.social_outreach.intent.execute_outreach_intent",
        return_value={
            "ok": True,
            "intent": "scout_and_draft",
            "message": "queued",
            "task_ids": [1],
            "plan": {"platform": "youtube"},
            "refused": False,
        },
    ):
        resp = client.post(
            "/api/social-outreach/intent",
            json={"text": "comment on youtube regarding Offline AI"},
        )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_intent_api_refuse_is_http_200(app, client):
    from backend.api import social_outreach_api

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with patch(
        "backend.services.social_outreach.intent.execute_outreach_intent",
        return_value={
            "ok": False,
            "intent": "refuse",
            "refused": True,
            "message": "Outreach: off-topic",
            "error": "off-topic",
            "task_ids": [],
            "queued": [],
        },
    ):
        resp = client.post(
            "/api/social-outreach/intent",
            json={"text": "did mary have a little lamb?"},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["refused"] is True
    assert body["ok"] is False
