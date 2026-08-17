"""Credential store: encryption, file modes, masking and the env overlay."""

import importlib
import os
import stat

import pytest

from backend.utils import credential_store as cs


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a temp dir and clear encryption opt-out."""
    monkeypatch.setenv("GUAARDVARK_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("GUAARDVARK_CREDENTIALS_PLAINTEXT", raising=False)
    importlib.reload(cs)
    yield
    importlib.reload(cs)


REF = "social:bluesky:default"


def test_round_trip_encrypted():
    cs.set_secret(REF, {"handle": "me.bsky.social", "app_password": "abcd-1234"})
    assert cs.get_secret(REF) == {"handle": "me.bsky.social", "app_password": "abcd-1234"}
    assert cs.get_status(REF)["encrypted"] is True


def test_ciphertext_is_not_plaintext_on_disk():
    cs.set_secret(REF, {"app_password": "super-secret-value"})
    raw = cs.credentials_path().read_text()
    assert "super-secret-value" not in raw


def test_file_and_key_modes_are_0600_and_dir_0700():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    assert stat.S_IMODE(os.stat(cs.credentials_path()).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cs.key_path()).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(cs.config_dir()).st_mode) == 0o700


def test_plaintext_mode_when_opted_out(monkeypatch):
    monkeypatch.setenv("GUAARDVARK_CREDENTIALS_PLAINTEXT", "1")
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    assert cs.get_secret(REF)["app_password"] == "abcd-1234"
    assert cs.get_status(REF)["encrypted"] is False
    assert not cs.key_path().exists()


def test_status_never_leaks_the_value():
    cs.set_secret(REF, {"app_password": "abcd-wxyz"})
    status = cs.get_status(REF)
    assert status["hint"] == "••••wxyz"
    assert "abcd-wxyz" not in repr(status)


def test_hint_field_selects_which_secret_is_hinted():
    cs.set_secret(
        REF,
        {"handle": "aaaa-1111", "app_password": "bbbb-2222"},
        hint_field="app_password",
    )
    assert cs.get_status(REF)["hint"] == "••••2222"


def test_blank_save_does_not_clear_the_token():
    cs.set_secret(REF, {"app_password": "keep-me-9999"})
    payload = {"app_password": "", "display_name": "Mine"}
    result = cs.apply_secret_updates(REF, payload, ["app_password"])

    assert result["updated"] == []
    assert result["skipped_empty"] is True
    assert cs.get_secret(REF)["app_password"] == "keep-me-9999"
    assert payload == {"display_name": "Mine"}, "secret must be popped from the payload"


def test_masked_hint_echoed_back_does_not_clear_the_token():
    cs.set_secret(REF, {"app_password": "keep-me-9999"})
    cs.apply_secret_updates(REF, {"app_password": "••••9999"}, ["app_password"])
    assert cs.get_secret(REF)["app_password"] == "keep-me-9999"


def test_update_merges_rather_than_replacing_other_fields():
    cs.set_secret(REF, {"handle": "me.bsky.social", "app_password": "old-0000"})
    cs.apply_secret_updates(REF, {"app_password": "new-1111"}, ["app_password"])
    secret = cs.get_secret(REF)
    assert secret["app_password"] == "new-1111"
    assert secret["handle"] == "me.bsky.social"


def test_env_overlay_wins_over_file_and_is_reported_as_env(monkeypatch):
    cs.set_secret("ai_provider:hf:default", {"token": "from-file"})
    monkeypatch.setenv("HF_TOKEN", "from-env")

    assert cs.get_secret("ai_provider:hf:default", env_keys=["HF_TOKEN"]) == {"token": "from-env"}
    status = cs.get_status("ai_provider:hf:default", env_keys=["HF_TOKEN"])
    assert status["source"] == "env"
    assert status["env_key"] == "HF_TOKEN"


def test_unconfigured_ref_reports_not_configured():
    status = cs.get_status("social:nope:default")
    assert status["configured"] is False
    assert status["source"] == "none"
    assert status["hint"] == ""


def test_delete_removes_the_record():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    assert cs.delete_secret(REF) is True
    assert cs.get_secret(REF) == {}
    assert cs.delete_secret(REF) is False


def test_empty_bundle_is_refused():
    with pytest.raises(ValueError):
        cs.set_secret(REF, {"app_password": "   "})


def test_corrupt_file_degrades_instead_of_raising():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    cs.credentials_path().write_text("{ this is not json")
    assert cs.get_secret(REF) == {}
    assert cs.get_status(REF)["configured"] is False


def test_missing_key_degrades_instead_of_raising():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    cs.key_path().unlink()
    # A fresh key cannot decrypt the old record, but nothing raises.
    assert cs.get_secret(REF) == {}


def test_inventory_readable_without_the_key():
    """The per-record envelope keeps metadata legible when secrets are not."""
    cs.set_secret(REF, {"handle": "x", "app_password": "abcd-1234"})
    cs.key_path().unlink()
    status = cs.get_status(REF)
    assert status["configured"] is True
    assert status["fields"] == ["app_password", "handle"]
    assert cs.list_refs() == [REF]


def test_atomic_write_leaves_no_tmp_file():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    assert not list(cs.config_dir().glob("*.tmp"))


def test_rotate_key_preserves_all_records():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    cs.set_secret("social:mastodon:default", {"access_token": "efgh-5678"})

    result = cs.rotate_key()
    assert result["error"] is None
    assert result["rotated"] == 2
    assert cs.get_secret(REF)["app_password"] == "abcd-1234"
    assert cs.get_secret("social:mastodon:default")["access_token"] == "efgh-5678"
    assert list(cs.config_dir().glob("secret.key.bak-*"))


def test_health_reports_mode_and_count():
    cs.set_secret(REF, {"app_password": "abcd-1234"})
    h = cs.health()
    assert h["mode"] == "0o600"
    assert h["record_count"] == 1
    assert h["encrypted"] is True


def test_credential_ref_shape():
    assert cs.credential_ref("social", "youtube") == "social:youtube:default"
    assert cs.credential_ref("social", "youtube", "brand") == "social:youtube:brand"
