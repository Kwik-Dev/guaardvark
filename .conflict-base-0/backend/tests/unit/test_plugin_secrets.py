"""Unit tests for plugin secret fields (.env, never plugin.json)."""

import os
from pathlib import Path

import pytest

from backend.utils.plugin_secrets import (
    PLUGIN_SECRET_FIELDS,
    apply_secret_updates,
    get_secret_status,
    secret_field_names,
)
from backend.services.interconnector_file_sync_service import (
    InterconnectorFileSyncService,
)


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    service = InterconnectorFileSyncService()
    monkeypatch.setattr(service, "get_project_root", lambda: tmp_path)
    return service


def test_discord_maps_bot_token():
    assert PLUGIN_SECRET_FIELDS["discord"]["bot_token"] == "DISCORD_BOT_TOKEN"
    assert "bot_token" in secret_field_names("discord")
    assert secret_field_names("ollama") == frozenset()


def test_status_unconfigured(svc, tmp_path):
    (tmp_path / ".env").write_text("FLASK_PORT=5000\n")
    # Clear process env so we don't pick up a live token from the host.
    old = os.environ.pop("DISCORD_BOT_TOKEN", None)
    try:
        status = get_secret_status("discord", file_sync_service=svc)
        assert status["bot_token"]["configured"] is False
        assert status["bot_token"]["hint"] == ""
    finally:
        if old is not None:
            os.environ["DISCORD_BOT_TOKEN"] = old


def test_status_masks_token(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("DISCORD_BOT_TOKEN=abcdefghijklmnopQRST\n")
    status = get_secret_status("discord", file_sync_service=svc)
    assert status["bot_token"]["configured"] is True
    assert status["bot_token"]["hint"] == "••••QRST"
    assert "abcdef" not in status["bot_token"]["hint"]


def test_apply_writes_token(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("FLASK_PORT=5000\n")
    payload = {"bot_token": "new-discord-token-xyz", "timeout": 45}
    result = apply_secret_updates("discord", payload, file_sync_service=svc)
    assert result["updated"] == ["bot_token"]
    assert "bot_token" not in payload  # stripped
    assert payload == {"timeout": 45}
    text = (tmp_path / ".env").read_text()
    assert "DISCORD_BOT_TOKEN=new-discord-token-xyz" in text
    assert os.environ.get("DISCORD_BOT_TOKEN") == "new-discord-token-xyz"
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)


def test_apply_empty_does_not_clear(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("DISCORD_BOT_TOKEN=keep-me-please-1234\n")
    payload = {"bot_token": "   ", "timeout": 30}
    result = apply_secret_updates("discord", payload, file_sync_service=svc)
    assert result["updated"] == []
    assert result["skipped_empty"] is True
    assert "bot_token" not in payload
    assert "DISCORD_BOT_TOKEN=keep-me-please-1234" in (tmp_path / ".env").read_text()


def test_apply_masked_hint_does_not_clear(svc, tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    (tmp_path / ".env").write_text("DISCORD_BOT_TOKEN=keep-me-please-1234\n")
    payload = {"bot_token": "••••1234"}
    result = apply_secret_updates("discord", payload, file_sync_service=svc)
    assert result["updated"] == []
    assert result["skipped_empty"] is True
    assert "DISCORD_BOT_TOKEN=keep-me-please-1234" in (tmp_path / ".env").read_text()


def test_secret_never_lands_in_manifest(svc, tmp_path, monkeypatch):
    """Simulate the API strip: after apply_secret_updates, registry must not see bot_token."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    from backend.plugins.plugin_registry import PluginRegistry

    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "discord"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        '{"id":"discord","name":"Discord","version":"1.0.0","type":"service",'
        '"config":{"service_url":"http://localhost:8200","timeout":30,'
        '"fallback_enabled":false,"default_enabled":true,"default_auto_start":false}}'
    )
    (tmp_path / ".env").write_text("")

    registry = PluginRegistry(plugins_dir=plugins_root)
    assert registry.is_registered("discord")

    payload = {"bot_token": "secret-token-value", "timeout": 60}
    apply_secret_updates("discord", payload, file_sync_service=svc)
    assert "bot_token" not in payload
    assert registry.update_plugin_config("discord", payload) is True
    saved = (plugin_dir / "plugin.json").read_text()
    assert "bot_token" not in saved
    assert "secret-token-value" not in saved
    assert "DISCORD_BOT_TOKEN=secret-token-value" in (tmp_path / ".env").read_text()
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
