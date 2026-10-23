"""UTM tagging on links to the profile's own site in outbound posts."""
from unittest.mock import patch, MagicMock

from backend.services.social_outreach import persona


def test_apply_utm_tags_adds_params_to_bare_url(outreach_profile):
    text = "Check out https://example.com for the demo."
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_source=reddit" in tagged
    assert "utm_medium=outreach" in tagged
    assert "utm_campaign=v253" in tagged


def test_apply_utm_tags_preserves_existing_params(outreach_profile):
    text = "https://example.com/release?ref=existing"
    tagged = persona.apply_utm_tags(text, platform="discord", campaign="v253")
    assert "ref=existing" in tagged
    assert "utm_source=discord" in tagged


def test_apply_utm_tags_skips_other_hosts_including_the_repo(outreach_profile):
    """The repo lives on a shared host (github.com), where a tag would land on
    other people's links too; only the site host is tagged."""
    text = "See https://github.com/example/app and https://reddit.com/r/x and https://notexample.com/"
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_" not in tagged


def test_apply_utm_tags_handles_subdomain(outreach_profile):
    text = "Read https://docs.example.com/quickstart"
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v253")
    assert "utm_source=reddit" in tagged


def test_apply_utm_tags_does_nothing_without_a_site(empty_outreach_profile):
    text = "Check out https://example.com for the demo."
    assert persona.apply_utm_tags(text, platform="reddit", campaign="v253") == text


def test_apply_utm_tags_takes_explicit_hosts(empty_outreach_profile):
    text = "https://a.test/x and https://b.test/y"
    tagged = persona.apply_utm_tags(text, platform="reddit", campaign="v1", hosts=["a.test"])
    assert "https://a.test/x?utm_source=reddit&utm_medium=outreach&utm_campaign=v1" in tagged
    assert "https://b.test/y" in tagged and "b.test/y?" not in tagged


def test_draft_outreach_text_applies_utm_via_persona_wrapper(outreach_profile):
    """End-to-end: a draft from persona.draft_outreach_text should have UTM-tagged links."""
    fake = MagicMock(message=MagicMock(content='{"comment": "Try it: https://example.com/release", "grade": 0.8, "rationale": "x"}'))
    with patch("backend.services.social_outreach.persona.ollama.chat", return_value=fake):
        out = persona.draft_outreach_text(
            platform="reddit",
            context={"url": "x", "title": "y", "body": "z"},
            tone="warm",
            campaign="v253",
        )
    assert "utm_source=reddit" in out["comment"]
