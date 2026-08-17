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


def test_valid_post_produces_no_problems():
    caps = Capabilities(max_text_chars=300, images=True, max_images=4, max_image_bytes=5000)
    assert validate_against(caps, [IMAGE], "hello") == []
