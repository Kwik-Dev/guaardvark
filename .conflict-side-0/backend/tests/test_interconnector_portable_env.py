"""Portable credential sync for Interconnector (HF_TOKEN etc.)."""
from pathlib import Path

import pytest

from backend.services.interconnector_file_sync_service import (
    InterconnectorFileSyncService,
    MAX_FILE_COUNT,
    PORTABLE_ENV_KEYS,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    service = InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    return service


def test_portable_env_keys_include_hf():
    assert "HF_TOKEN" in PORTABLE_ENV_KEYS


def test_content_pull_cap_covers_current_inventory():
    # Regression: inventory was 1544+ and the old 1500 cap truncated the tail.
    assert MAX_FILE_COUNT >= 5000


def test_read_portable_env_credentials_allowlist_only(svc, tmp_path):
    (tmp_path / ".env").write_text(
        "HF_TOKEN=hf_test_token\n"
        "DATABASE_URL=postgresql://local/db\n"
        "SECRET_KEY=do-not-sync\n"
        "ANTHROPIC_API_KEY=sk-test\n"
        "FLASK_PORT=5000\n"
    )
    creds = svc.read_portable_env_credentials()
    assert creds == {
        "HF_TOKEN": "hf_test_token",
        "ANTHROPIC_API_KEY": "sk-test",
    }


def test_read_portable_env_skips_empty(svc, tmp_path):
    (tmp_path / ".env").write_text("HF_TOKEN=\nDISCORD_BOT_TOKEN=  \n")
    assert svc.read_portable_env_credentials() == {}


def test_merge_adds_and_updates(svc, tmp_path):
    (tmp_path / ".env").write_text(
        "FLASK_PORT=5000\n"
        "HF_TOKEN=old_token\n"
        "DATABASE_URL=postgresql://local/db\n"
    )
    result = svc.merge_portable_env_credentials({
        "HF_TOKEN": "new_token",
        "ANTHROPIC_API_KEY": "sk-new",
        "DATABASE_URL": "postgresql://evil/db",  # must be ignored
        "SECRET_KEY": "nope",
    })
    text = (tmp_path / ".env").read_text()
    assert "HF_TOKEN=new_token" in text
    assert "ANTHROPIC_API_KEY=sk-new" in text
    assert "DATABASE_URL=postgresql://local/db" in text
    assert "SECRET_KEY=nope" not in text
    assert "FLASK_PORT=5000" in text
    assert result["updated"] == ["HF_TOKEN"]
    assert result["added"] == ["ANTHROPIC_API_KEY"]
    assert "DATABASE_URL" in result["skipped"]
    assert "SECRET_KEY" in result["skipped"]


def test_merge_creates_env_with_0600(svc, tmp_path):
    assert not (tmp_path / ".env").exists()
    svc.merge_portable_env_credentials({"HF_TOKEN": "hf_only"})
    env = tmp_path / ".env"
    assert env.is_file()
    assert env.read_text().strip() == "HF_TOKEN=hf_only"
    assert oct(env.stat().st_mode & 0o777) == "0o600"
