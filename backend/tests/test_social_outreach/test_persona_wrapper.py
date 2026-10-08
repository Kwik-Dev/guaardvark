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


def test_without_a_pitch_file_the_sheet_is_built_from_the_feature_blurbs():
    """A fresh install has no PITCH.md; the framing still promises a pitch sheet,
    so one is built from the one-line pitch and every feature blurb."""
    with patch("backend.services.social_outreach.persona._load_pitch_md", return_value=""):
        system = persona._compose_outward_facing_system()
    assert "--- PITCH SHEET ---" in system
    assert persona.GUAARDVARK_PITCH in system
    for blurb in persona.FEATURE_BLURBS.values():
        assert blurb in system


# ---- a feature a person asked for steers the draft ------------------------------

DOC_SEARCH_THREAD = {
    "thread_context": "How do I search my own PDFs offline without uploading them?",
    "url": "https://reddit.com/x",
}


def _prompts(**kwargs):
    """Draft with a stand-in LLM; return the (system, user) prompts it was given."""
    seen = []

    def llm(system, user):
        seen.append((system, user))
        return {"draft": "d", "grade": 0.8, "reason": "r"}

    with patch("backend.services.social_outreach.persona._load_pitch_md", return_value="pitch"):
        persona.draft_outreach_text(llm=llm, **kwargs)
    return seen[0]


def test_a_requested_feature_leads_the_comment_prompt():
    _, user = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, requested_feature="rag")

    assert f"REQUESTED FEATURE: rag ({persona.FEATURE_BLURBS['rag']})" in user
    assert "If it fits this thread and the pitch sheet backs it, lead with it." in user
    assert "grade below 0.3 and say in reason why it does not fit" in user


def test_a_requested_feature_in_plain_words_is_passed_as_written():
    _, user = _prompts(
        platform="reddit", context=DOC_SEARCH_THREAD,
        requested_feature="Voice cloning from a short clip",
    )

    assert "REQUESTED FEATURE: Voice cloning from a short clip\n" in user


def test_without_a_requested_feature_the_comment_prompt_is_as_before():
    """No request, a blank one, or only recon's keyword label: the drafter
    picks its angle from the pitch sheet and the prompt carries no request."""
    _, plain = _prompts(platform="reddit", context=DOC_SEARCH_THREAD)
    _, labelled = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, feature_hint="rag")
    _, blank = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, requested_feature="  ")

    assert "REQUESTED FEATURE" not in plain
    assert "only fits sometimes.\n\nRespond with JSON" in plain
    assert labelled == plain
    assert blank == plain


def test_a_requested_feature_leads_a_share_post():
    share = {"target": "r/LocalLLaMA", "link_url": persona.SITE_URL}
    _, asked = _prompts(platform="reddit", mode="share", context=share, requested_feature="video_gen")
    _, plain = _prompts(platform="reddit", mode="share", context=share)

    assert f"REQUESTED FEATURE: video_gen ({persona.FEATURE_BLURBS['video_gen']})" in asked
    assert "If it fits this community" in asked
    assert "REQUESTED FEATURE" not in plain


def test_a_reply_ignores_a_requested_feature():
    reply = {"parent_text": "p", "incoming_text": "nice video", "incoming_author": "a"}
    _, user = _prompts(platform="youtube", mode="reply", context=reply, requested_feature="rag")

    assert "REQUESTED FEATURE" not in user
