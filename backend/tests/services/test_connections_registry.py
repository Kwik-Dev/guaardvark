"""Provider registry contract and capability-driven validation."""

import pytest

from backend.services.connections import registry
from backend.services.connections.base import Capabilities, MediaItem
from backend.services.connections.media import validate_against

ALL_PROVIDERS = sorted(registry.PROVIDER_MODULES)


def test_every_declared_provider_loads():
    loaded = {s.provider for s in registry.list_specs()}
    assert loaded == set(ALL_PROVIDERS)


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_provider_exposes_the_interface(provider):
    module = registry.get_provider(provider)
    assert callable(getattr(module, "test", None))
    assert callable(getattr(module, "publish", None))


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_spec_is_well_formed(provider):
    spec = registry.spec_for(provider)
    assert spec.provider == provider
    assert spec.label and spec.auth_kinds
    names = [f.name for f in spec.credential_fields]
    assert len(names) == len(set(names)), "duplicate credential field"
    if spec.hint_field:
        assert spec.hint_field in names, "hint_field must name a real credential field"


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_spec_serialises_without_secrets(provider):
    payload = registry.spec_for(provider).to_dict()
    assert payload["capabilities"]
    for field in payload["credential_fields"]:
        assert field["secret"] is True
    for field in payload["config_fields"]:
        assert field["secret"] is False


def test_unknown_provider_raises_cleanly():
    with pytest.raises(KeyError):
        registry.get_provider("does-not-exist")


def test_social_platforms_lists_provider_ids():
    assert set(registry.social_platforms()) == set(ALL_PROVIDERS)


def test_youtube_defaults_to_private():
    caps = registry.spec_for("youtube").capabilities
    assert caps.default_visibility == "private"
    assert caps.requires_media is True


# --- the provider contract (base.py) -----------------------------------------

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_provider_honours_the_contract(provider):
    assert registry.contract_problems(provider) == []


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_rate_limits_name_their_source(provider):
    for limit in registry.spec_for(provider).capabilities.rate_limits:
        assert limit.posts >= 1 and limit.window_seconds >= 60
        assert limit.source.startswith("https://")
        assert limit.platform_limit


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_new_capabilities_serialise(provider):
    caps = registry.spec_for(provider).to_dict()["capabilities"]
    for key in ("rate_limits", "ai_disclosure", "bot_label", "native_scheduling", "deletable",
                "delete_window_seconds", "requires_title", "max_title_chars", "schedule_refusal",
                "public_opt_in"):
        assert key in caps


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_setup_and_post_fields_serialise(provider):
    payload = registry.spec_for(provider).to_dict()
    assert isinstance(payload["setup_steps"], list) and isinstance(payload["notice"], str)
    for field in payload["post_fields"]:
        assert field["name"] and field["label"]


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_no_provider_but_an_owned_channel_skips_approval(provider):
    assert registry.spec_for(provider).posts_to_owned_channel == (provider == "discord_webhook")


def test_contract_flags_a_declaration_without_its_function(monkeypatch):
    import types

    from backend.services.connections.base import Capabilities, ProviderSpec, RateLimit

    spec = ProviderSpec(
        provider="fake", family="social", label="Fake", auth_kinds=("api_token",),
        capabilities=Capabilities(
            deletable=True,
            rate_limits=(RateLimit(posts=1, window_seconds=60, platform_limit="x", source="https://x"),),
        ),
    )
    module = types.SimpleNamespace(SPEC=spec, test=lambda ctx: None, publish=lambda *a: None)
    monkeypatch.setitem(registry._cache, "fake", module)
    assert registry.contract_problems("fake") == ["deletable is declared but delete() is missing."]


def test_native_scheduling_where_the_platform_has_it():
    assert registry.spec_for("mastodon").capabilities.native_scheduling.min_lead_seconds == 300
    youtube = registry.spec_for("youtube").capabilities.native_scheduling
    assert youtube.field == "status.publishAt" and youtube.visibilities == ("public",)
    for provider in ("bluesky", "telegram", "discord_webhook", "linkedin"):
        assert registry.spec_for(provider).capabilities.native_scheduling is None


def test_only_linkedin_rules_out_scheduled_posts():
    refusing = [s.provider for s in registry.list_specs() if s.capabilities.schedule_refusal]
    assert refusing == ["linkedin"]


def test_disclosure_rules():
    from backend.services.connections.base import DISCLOSURE_FLAG

    youtube = registry.spec_for("youtube").capabilities.ai_disclosure
    assert youtube.mechanism == DISCLOSURE_FLAG and youtube.flag == "status.containsSyntheticMedia"
    assert youtube.required_for(True, "ui") and not youtube.required_for(False, "chat")
    mastodon = registry.spec_for("mastodon").capabilities.ai_disclosure
    assert mastodon.required_for(False, "chat") and mastodon.required_for(True, "ui")
    assert not mastodon.required_for(False, "ui")
    assert not registry.spec_for("bluesky").capabilities.ai_disclosure.required_for(True, "chat")


def test_telegram_declares_its_delete_window():
    assert registry.spec_for("telegram").capabilities.delete_window_seconds == 48 * 3600


# --- capability validation ---------------------------------------------------

IMAGE = MediaItem(path="/tmp/a.png", mime="image/png", bytes=1000)
VIDEO = MediaItem(path="/tmp/a.mp4", mime="video/mp4", bytes=1000)


def test_text_over_limit_is_rejected():
    caps = Capabilities(max_text_chars=10)
    assert validate_against(caps, [], "x" * 11)
    assert validate_against(caps, [], "x" * 10) == []


def test_video_to_an_images_only_target_is_rejected():
    caps = Capabilities(images=True, max_images=4)
    problems = validate_against(caps, [VIDEO], "hi")
    assert any("video" in p.lower() for p in problems)


def test_too_many_images_is_rejected():
    caps = Capabilities(images=True, max_images=1)
    problems = validate_against(caps, [IMAGE, IMAGE], "hi")
    assert any("limit is 1" in p for p in problems)


def test_oversized_image_is_rejected():
    caps = Capabilities(images=True, max_images=4, max_image_bytes=500)
    assert validate_against(caps, [IMAGE], "hi")


def test_required_media_is_enforced():
    caps = Capabilities(video=True, requires_media=True)
    problems = validate_against(caps, [], "hi")
    assert any("requires at least one" in p for p in problems)


def test_unsupported_visibility_is_rejected():
    caps = Capabilities(visibilities=("public", "unlisted"))
    problems = validate_against(caps, [], "hi", visibility="private")
    assert any("not supported" in p for p in problems)
    assert validate_against(caps, [], "hi", visibility="public") == []


def test_title_on_a_target_without_titles_is_rejected():
    problems = validate_against(Capabilities(), [], "hi", title="A title")
    assert any("title" in p.lower() for p in problems)


def test_empty_post_is_rejected():
    problems = validate_against(Capabilities(), [], "   ")
    assert any("Nothing to post" in p for p in problems)


def test_disallowed_mime_is_rejected():
    caps = Capabilities(images=True, max_images=4, accepted_mime=("image/jpeg",))
    problems = validate_against(caps, [IMAGE], "hi")
    assert any("image/png" in p for p in problems)


def test_a_required_title_and_its_limit_are_enforced():
    caps = Capabilities(supports_title=True, requires_title=True, max_title_chars=5)
    assert "This target needs a title." in validate_against(caps, [], "hi")
    assert "This target needs a title." in validate_against(caps, [], "hi", title="   ")
    assert any("the limit is 5" in p for p in validate_against(caps, [], "hi", title="too long"))
    assert validate_against(caps, [], "hi", title="ok") == []


def test_a_post_field_checks_presence_length_and_pattern():
    from backend.services.connections.base import PostField

    field = PostField(name="room", label="Room", required=True, pattern=r"[a-z]+",
                      pattern_message="Lowercase letters only.", max_chars=6)
    assert field.problems("") == ["Room is required."]
    assert field.problems("abcdefg") == ["Room is 7 characters; the limit is 6."]
    assert field.problems("AB") == ["Lowercase letters only."]
    assert field.problems(" abc ") == []
    assert PostField(name="note", label="Note").problems("") == []


def test_valid_post_produces_no_problems():
    caps = Capabilities(max_text_chars=300, images=True, max_images=4, max_image_bytes=5000)
    assert validate_against(caps, [IMAGE], "hello") == []
