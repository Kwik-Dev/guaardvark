"""The local "what you promote" profile: load, save, validation and the
answers the outreach surfaces read from it."""
import json

import pytest

from backend.services.social_outreach import profile


@pytest.fixture
def profile_file(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    monkeypatch.setenv(profile.PROFILE_PATH_ENV, str(path))
    return path


FULL = {
    "name": "Example App",
    "pitch": "Local-first AI.\nRuns on your box.",
    "site_url": "https://example.com",
    "repo_url": "https://github.com/example/app",
    "account_handle": "@example",
    "videos": [{"label": "Demo", "url": "https://www.youtube.com/watch?v=abcdefghijk"}],
    "features": [
        {"key": "Local AI", "blurb": "runs locally", "keywords": "ollama, local llm, c++"},
        {"key": "rag", "blurb": "docs search", "keywords": ["rag"]},
    ],
}


def test_a_fresh_install_has_an_empty_profile(profile_file):
    p = profile.load()
    assert p.to_dict() == {
        "name": "", "pitch": "", "site_url": "", "repo_url": "", "account_handle": "",
        "videos": [], "features": [],
    }
    assert not p.is_configured()
    assert profile.site_url() == ""
    assert profile.cta_url("youtube") == ""
    assert profile.feature_blurbs() == {}
    assert profile.link_hosts() == []
    assert profile.find_relevant_feature("ollama") is None
    assert p.pitch_sheet() == ""


def test_save_normalises_and_writes_atomically(profile_file):
    saved = profile.save(FULL)

    assert profile_file.exists()
    assert not profile_file.with_name("profile.json.tmp").exists()
    assert json.loads(profile_file.read_text()) == saved.to_dict()
    assert saved.account_handle == "example"
    assert saved.features[0] == {"key": "local_ai", "blurb": "runs locally", "keywords": ["ollama", "local llm", "c++"]}
    assert saved.features[1]["keywords"] == ["rag"]
    assert saved.is_configured()


def test_load_sees_a_new_save_without_a_restart(profile_file):
    profile.save(FULL)
    assert profile.load().name == "Example App"
    profile.save({"name": "Two", "pitch": "p"})
    assert profile.load().name == "Two"
    assert profile.site_url() == ""


def test_links_and_hosts(profile_file):
    p = profile.save(FULL)
    assert p.cta_url("youtube") == "https://github.com/example/app"
    assert p.cta_url("reddit") == "https://example.com"
    assert p.link_hosts() == ["example.com"]
    assert p.own_links() == ["example.com", "github.com/example/app"]

    only_repo = profile.save({"name": "n", "pitch": "p", "repo_url": "https://github.com/example/app"})
    assert only_repo.cta_url("reddit") == "https://github.com/example/app"
    assert only_repo.link_hosts() == []


def test_keywords_match_whole_words_and_are_escaped(profile_file):
    profile.save(FULL)
    assert profile.find_relevant_feature("Running C++ and Ollama") == "local_ai"
    assert profile.find_relevant_feature("a local LLM setup") == "local_ai"
    assert profile.find_relevant_feature("try RAG") == "rag"
    assert profile.find_relevant_feature("ragtime music") is None
    assert profile.find_relevant_feature("c+ only") is None
    assert profile.find_relevant_feature("") is None


def test_pitch_sheet_lists_the_facts_a_draft_may_use(profile_file):
    sheet = profile.save(FULL).pitch_sheet()
    assert sheet.startswith("Example App: Local-first AI.\nRuns on your box.")
    assert "- runs locally\n- docs search" in sheet
    assert "- Site: https://example.com\n- Repo: https://github.com/example/app" in sheet


@pytest.mark.parametrize("bad, message", [
    ({"site_url": "ftp://example.com"}, "site_url must be an http or https URL"),
    ({"repo_url": "example.com"}, "repo_url must be an http or https URL"),
    ({"videos": [{"label": "x", "url": "javascript:alert(1)"}]}, "video 1 url must be an http or https URL"),
    ({"videos": "x"}, "videos must be a list"),
    ({"features": [{"key": "a", "blurb": "b"}, {"key": "a", "blurb": "c"}]}, "feature key 'a' is used twice"),
    ({"features": [1]}, "each feature must be an object"),
    ("not an object", "profile must be an object"),
])
def test_save_rejects_bad_input_with_a_plain_message(profile_file, bad, message):
    with pytest.raises(ValueError, match=message):
        profile.save(bad)
    assert not profile_file.exists()


def test_save_caps_lengths_and_lists_and_strips_control_characters(profile_file):
    saved = profile.save({
        "name": "Ex\x00am\x07ple " + "n" * 200,
        "pitch": "line one\n\x1bline two",
        "videos": [{"label": "v", "url": "https://example.com/v"}] * 25,
        "features": [{"key": f"k{i}", "blurb": "b", "keywords": [f"w{j}" for j in range(30)]} for i in range(45)],
    })
    assert saved.name.startswith("Example n")
    assert len(saved.name) == profile.MAX_NAME
    assert saved.pitch == "line one\nline two"
    assert len(saved.videos) == profile.MAX_VIDEOS
    assert len(saved.features) == profile.MAX_FEATURES
    assert len(saved.features[0]["keywords"]) == profile.MAX_KEYWORDS


def test_an_unreadable_file_loads_as_empty(profile_file):
    profile_file.write_text("{not json")
    assert profile.load().to_dict()["name"] == ""
