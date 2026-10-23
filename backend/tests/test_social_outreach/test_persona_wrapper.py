"""Persona wrapper: the system message carries the framing and the pitch sheet
(PITCH.md, else the profile); the comment prompt carries no keyword-picked
feature hint; with nothing to promote, nothing is drafted."""
from unittest.mock import patch, MagicMock

from backend.services.social_outreach import persona

NO_PITCH_MD = patch("backend.services.social_outreach.persona._load_pitch_md", return_value="")


def test_draft_outreach_text_injects_system_block(outreach_profile):
    fake_resp = MagicMock(message=MagicMock(content='{"comment": "hi", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake_resp) as m:
        out = persona.draft_outreach_text(
            platform="reddit",
            context={"url": "https://reddit.com/x", "title": "test", "body": "test"},
            tone="warm",
        )
    assert m.called
    assert out["draft"] == "hi"
    messages = m.call_args.kwargs["messages"]
    system_msg = next((msg for msg in messages if msg["role"] == "system"), None)
    assert system_msg is not None
    assert system_msg["content"].startswith("You are writing a comment or post under the Example App account.\n")
    assert persona.OUTWARD_FACING_SYSTEM_BLOCK in system_msg["content"]


def test_comment_prompt_has_the_pitch_sheet_and_no_recon_hint(outreach_profile):
    """The drafter picks its angle from the pitch sheet in the system message;
    neither a keyword match in the thread ("ollama") nor a caller's
    feature_hint puts a RECON HINT or a feature key in the prompt."""
    pitch = "Example App runs models on your own GPU."
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


def test_without_a_pitch_file_the_sheet_is_built_from_the_profile(outreach_profile):
    """With no PITCH.md the framing still promises a pitch sheet, so one is
    built from the profile: name, pitch, every feature blurb and the links."""
    with NO_PITCH_MD:
        system = persona._compose_outward_facing_system()
    assert "--- PITCH SHEET ---" in system
    assert f"{outreach_profile.name}: {outreach_profile.pitch}" in system
    for blurb in outreach_profile.feature_blurbs().values():
        assert blurb in system
    assert "- Site: https://example.com" in system
    assert "- Repo: https://github.com/example/app" in system


def test_a_pitch_file_wins_over_the_profile(outreach_profile):
    with patch("backend.services.social_outreach.persona._load_pitch_md", return_value="the sheet"):
        system = persona._compose_outward_facing_system()
    assert "--- PITCH SHEET ---\nthe sheet\n" in system
    assert outreach_profile.pitch not in system


# ---- a fresh install promotes nothing --------------------------------------

def _no_llm(system, user):
    raise AssertionError("the LLM must not be called")


def test_with_nothing_to_promote_no_comment_is_drafted(empty_outreach_profile):
    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="reddit", context={"title": "t", "body": "b"}, llm=_no_llm,
        )
    assert out["draft"] == "" and out["comment"] == ""
    assert out["grade"] == 0.0
    assert out["reason"] == 'Nothing to promote yet: fill in "What you promote" on the Outreach page'


def test_with_nothing_to_promote_no_share_is_drafted(empty_outreach_profile):
    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="reddit", mode="share", context={"target": "r/x"}, llm=_no_llm,
        )
    assert out["draft"] == ""
    assert out["reason"].startswith("Nothing to promote yet")


def test_a_reply_on_an_own_video_needs_no_pitch_sheet(empty_outreach_profile):
    """Replies do not pitch, so they are drafted without a profile; the
    framing then names no product."""
    seen = []

    def llm(system, user):
        seen.append(system)
        return {"draft": "thanks", "grade": 0.8, "reason": "r"}

    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="youtube", mode="reply",
            context={"parent_text": "p", "incoming_text": "nice", "incoming_author": "a"}, llm=llm,
        )
    assert out["draft"] == "thanks"
    assert seen[0].startswith("You are writing a REPLY to a comment left on one of your own YouTube videos.\n")
    assert "PITCH SHEET" not in seen[0]


def test_a_share_with_no_link_is_skipped(outreach_profile, monkeypatch):
    from backend.services.social_outreach import profile
    profile.save({"name": "Example App", "pitch": "p"})
    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="reddit", mode="share", context={"target": "r/x"}, llm=_no_llm,
        )
    assert out["draft"] == ""
    assert out["reason"] == 'no link to share: add a site URL under "What you promote" on the Outreach page'


def test_a_share_uses_the_profile_site_by_default(outreach_profile):
    seen = []

    def llm(system, user):
        seen.append(user)
        return {"title": "t", "body": "b", "grade": 0.8, "reason": "r"}

    with NO_PITCH_MD:
        out = persona.draft_outreach_text(platform="reddit", mode="share", context={"target": "r/x"}, llm=llm)
    assert "LINK: https://example.com\n" in seen[0]
    assert "introducing Example App to a relevant subreddit" in seen[0]
    assert '"link_url": "https://example.com?utm_source=reddit&utm_medium=outreach&utm_campaign=v253"' in out["draft"]


def test_a_linked_comment_with_no_link_is_skipped(outreach_profile):
    from backend.services.social_outreach import profile
    profile.save({"name": "Example App", "pitch": "p"})
    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="youtube", context={"thread_context": "x"}, include_link=True, llm=_no_llm,
        )
    assert out["draft"] == ""
    assert out["reason"].startswith("no link to share")


def test_a_linked_youtube_comment_cites_the_repo(outreach_profile):
    seen = []

    def llm(system, user):
        seen.append(user)
        return {"draft": "good video", "grade": 0.9, "reason": "r"}

    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="youtube", context={"thread_context": "x"}, include_link=True, llm=llm,
        )
    assert "include a link to https://github.com/example/app where it fits naturally — one short mention of Example App" in seen[0]
    # The model left the link out, so it is appended; repo links are not UTM-tagged.
    assert out["draft"] == "good video\n\nhttps://github.com/example/app"


def test_a_comment_that_already_cites_the_site_gets_no_second_link(outreach_profile):
    def llm(system, user):
        return {"draft": "see example.com/docs", "grade": 0.9, "reason": "r"}

    with NO_PITCH_MD:
        out = persona.draft_outreach_text(
            platform="reddit", context={"thread_context": "x"}, include_link=True, llm=llm,
        )
    assert out["draft"] == "see example.com/docs"


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


def test_a_requested_feature_leads_the_comment_prompt(outreach_profile):
    _, user = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, requested_feature="rag")

    assert f"REQUESTED FEATURE: rag ({outreach_profile.feature_blurbs()['rag']})" in user
    assert "If it fits this thread and the pitch sheet backs it, lead with it." in user
    assert "grade below 0.3 and say in reason why it does not fit" in user


def test_a_requested_feature_in_plain_words_is_passed_as_written(outreach_profile):
    _, user = _prompts(
        platform="reddit", context=DOC_SEARCH_THREAD,
        requested_feature="Voice cloning from a short clip",
    )

    assert "REQUESTED FEATURE: Voice cloning from a short clip\n" in user


def test_without_a_requested_feature_the_comment_prompt_is_as_before(outreach_profile):
    """No request, a blank one, or only recon's keyword label: the drafter
    picks its angle from the pitch sheet and the prompt carries no request."""
    _, plain = _prompts(platform="reddit", context=DOC_SEARCH_THREAD)
    _, labelled = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, feature_hint="rag")
    _, blank = _prompts(platform="reddit", context=DOC_SEARCH_THREAD, requested_feature="  ")

    assert "REQUESTED FEATURE" not in plain
    assert "a mention of Example App is optional and only fits sometimes.\n\nRespond with JSON" in plain
    assert labelled == plain
    assert blank == plain


def test_a_requested_feature_leads_a_share_post(outreach_profile):
    share = {"target": "r/LocalLLaMA", "link_url": outreach_profile.site_url}
    _, asked = _prompts(platform="reddit", mode="share", context=share, requested_feature="video_gen")
    _, plain = _prompts(platform="reddit", mode="share", context=share)

    assert f"REQUESTED FEATURE: video_gen ({outreach_profile.feature_blurbs()['video_gen']})" in asked
    assert "If it fits this community" in asked
    assert "REQUESTED FEATURE" not in plain


def test_a_reply_ignores_a_requested_feature(outreach_profile):
    reply = {"parent_text": "p", "incoming_text": "nice video", "incoming_author": "a"}
    _, user = _prompts(platform="youtube", mode="reply", context=reply, requested_feature="rag")

    assert "REQUESTED FEATURE" not in user
