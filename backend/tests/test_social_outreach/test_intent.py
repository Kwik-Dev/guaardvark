"""Natural-language outreach intent parsing + API."""
from unittest.mock import patch


def test_parse_youtube_offline_ai_comfyui():
    from backend.services.social_outreach.intent import parse_outreach_intent

    plan = parse_outreach_intent(
        "comment on some youtube videos regarding Offline AI or ComfyUI"
    )
    assert plan["platform"] == "youtube"
    assert plan["action"] == "comment"
    assert "Offline AI" in plan["topics"]
    assert "ComfyUI" in plan["topics"]
    assert plan["chain_draft"] is True


def test_execute_intent_queues_youtube(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

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
        )
        assert result["ok"] is True
        assert 42 in result["task_ids"]
        mock_q.assert_called_once()
        kwargs = mock_q.call_args
        assert kwargs[0][0] == "youtube"
        assert "Offline AI" in kwargs[1]["keyword_profiles"]
        assert "ComfyUI" in kwargs[1]["keyword_profiles"]
        assert kwargs[1]["chain_draft"] is True


def test_execute_intent_kill_switch_off(app):
    from backend.services.social_outreach.intent import execute_outreach_intent

    with app.app_context(), \
         patch("backend.services.social_outreach.intent.kill_switch.is_enabled", return_value=False):
        result = execute_outreach_intent("scout youtube about ollama")
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()


def test_intent_api_endpoint(app, client):
    from backend.api import social_outreach_api

    app.register_blueprint(social_outreach_api.social_outreach_bp)

    with patch(
        "backend.services.social_outreach.intent.execute_outreach_intent",
        return_value={
            "ok": True,
            "message": "queued",
            "task_ids": [1],
            "plan": {"platform": "youtube"},
        },
    ):
        resp = client.post(
            "/api/social-outreach/intent",
            json={"text": "comment on youtube regarding Offline AI"},
        )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
