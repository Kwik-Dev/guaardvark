"""Persona wrapper: the system message carries the framing and the pitch sheet;
the comment prompt carries no keyword-picked feature hint."""
from unittest.mock import patch, MagicMock

from backend.services.social_outreach import persona


def test_draft_outreach_text_injects_system_block():
    fake_resp = MagicMock(message=MagicMock(content='{"comment": "hi", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake_resp) as m:
        out = persona.draft_outreach_text(
            platform="reddit",
            context={"url": "https://reddit.com/x", "title": "test", "body": "test"},
            tone="warm",
        )
    assert m.called
    messages = m.call_args.kwargs.get("messages") or m.call_args.args[1] if len(m.call_args.args) > 1 else m.call_args.kwargs["messages"]
    system_msg = next((msg for msg in messages if msg["role"] == "system"), None)
    assert system_msg is not None
    assert persona.OUTWARD_FACING_SYSTEM_BLOCK in system_msg["content"]


def test_comment_prompt_has_the_pitch_sheet_and_no_recon_hint():
    """The drafter picks its angle from the pitch sheet in the system message;
    neither a keyword match in the thread ("ollama") nor a caller's
    feature_hint puts a RECON HINT or a feature key in the prompt."""
    pitch = "Guaardvark runs models on your own GPU."
    fake_resp = MagicMock(message=MagicMock(content='{"comment": "hi", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona._load_pitch_md", return_value=pitch), \
         patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake_resp) as m:
        persona.draft_outreach_text(
            platform="reddit",
            context={"url": "https://reddit.com/x", "title": "ollama question", "body": "vram"},
            tone="warm",
            feature_hint="video_gen",
        )
    messages = m.call_args.kwargs["messages"]
    system_msg = next(msg for msg in messages if msg["role"] == "system")
    user_msg = next(msg for msg in messages if msg["role"] == "user")
    assert f"--- PITCH SHEET ---\n{pitch}" in system_msg["content"]
    assert "ollama question" in user_msg["content"]
    assert "RECON HINT" not in user_msg["content"]
    assert "video_gen" not in user_msg["content"]
    assert persona.find_relevant_feature("ollama question") == "local_ai"
    assert "local_ai" not in user_msg["content"]
