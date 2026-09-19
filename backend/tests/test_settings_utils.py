"""Tests for get_setting / save_setting utility."""
import json
import pytest
from unittest.mock import patch, MagicMock


class TestGetSetting:
    """Test the unified get_setting function."""

    def test_returns_default_when_no_app_context(self):
        """Outside Flask app context, returns default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=False):
            result = get_setting("enhanced_context_enabled", default=True, cast=bool)
            assert isinstance(result, bool)

    def test_reads_from_settings_table(self):
        """Keys not in SYSTEM_SETTING_KEYS read from Setting model."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "false"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                assert result is False

    def test_reads_from_system_settings_table(self):
        """Keys in SYSTEM_SETTING_KEYS read from SystemSetting model."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "always"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("claude_escalation_mode", default="manual")
                assert result == "always"

    def test_falls_back_to_env_var(self):
        """When DB has no value, reads from mapped env var."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                with patch.dict("os.environ", {"GUAARDVARK_ENHANCED_CONTEXT": "false"}):
                    result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                    assert result is False

    def test_falls_back_to_default(self):
        """When DB and env var both missing, returns default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("GUAARDVARK_ADVANCED_RAG", None)
                    result = get_setting("advanced_rag_enabled", default=True, cast=bool)
                    assert result is True

    def test_cast_bool_truthy_values(self):
        """Bool cast handles 'true', 'True', '1', 'yes'."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                for truthy in ["true", "True", "1", "yes"]:
                    mock_setting.value = truthy
                    mock_db.session.get.return_value = mock_setting
                    assert get_setting("advanced_rag_enabled", default=False, cast=bool) is True

    def test_cast_bool_falsy_values(self):
        """Bool cast handles 'false', 'False', '0', 'no', ''."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                for falsy in ["false", "False", "0", "no", ""]:
                    mock_setting.value = falsy
                    mock_db.session.get.return_value = mock_setting
                    assert get_setting("advanced_rag_enabled", default=True, cast=bool) is False

    def test_cast_int(self):
        """Int cast works for budget values."""
        from backend.utils.settings_utils import get_setting
        mock_setting = MagicMock()
        mock_setting.value = "500000"
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = mock_setting
                result = get_setting("claude_monthly_budget", default=1000000, cast=int)
                assert result == 500000

    def test_db_exception_falls_back_gracefully(self):
        """DB errors should not crash -- fall back to env/default."""
        from backend.utils.settings_utils import get_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.side_effect = Exception("DB down")
                result = get_setting("enhanced_context_enabled", default=True, cast=bool)
                assert result is True


class TestSaveSetting:
    """Test save_setting function."""

    def test_save_to_settings_table(self):
        """Non-system keys save to Setting model."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                save_setting("advanced_rag_enabled", "false")
                mock_db.session.add.assert_called_once()
                mock_db.session.commit.assert_called_once()

    def test_save_to_system_settings_table(self):
        """System keys save to SystemSetting model."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.return_value = None
                save_setting("claude_token_usage", '{"usage": {}}')
                mock_db.session.add.assert_called_once()
                mock_db.session.commit.assert_called_once()

    def test_no_crash_outside_app_context(self):
        """save_setting outside app context logs warning, doesn't crash."""
        from backend.utils.settings_utils import save_setting
        with patch("backend.utils.settings_utils.has_app_context", return_value=False):
            save_setting("test_key", "test_value")


class TestChatImageModel:
    def test_default_is_auto(self):
        from backend.utils.settings_utils import get_chat_image_model

        with patch("backend.utils.settings_utils.get_setting", return_value="auto"):
            assert get_chat_image_model() == "auto"

    def test_reads_persisted_value(self):
        from backend.utils.settings_utils import get_chat_image_model

        with patch("backend.utils.settings_utils.get_setting", return_value="zimage-turbo"):
            assert get_chat_image_model() == "zimage-turbo"


class TestActiveVideoModelSettings:
    def test_empty_means_inherit(self):
        from backend.utils.settings_utils import get_active_video_model, get_active_video_model_overrides

        with patch("backend.utils.settings_utils.get_setting", return_value=""):
            assert get_active_video_model() == ""
            assert get_active_video_model_overrides() == {
                "i2v": "", "music_video": "", "film_crew": "",
            }

    def test_reads_persisted_global(self):
        from backend.utils.settings_utils import get_active_video_model

        with patch("backend.utils.settings_utils.get_setting", return_value="wan22-5b"):
            assert get_active_video_model() == "wan22-5b"


class TestSecretRedaction:
    """A failed settings write must not put the credential in the log."""

    def test_secret_key_shapes_are_recognised(self):
        from backend.utils.settings_utils import is_secret_key
        for key in (
            "address_provider_key",
            "openai_api_key",
            "GITHUB_TOKEN",
            "client_secret",
            "smtp_password",
            "DB_PASSWORD_FILE",
        ):
            assert is_secret_key(key), key

    def test_ordinary_keys_are_not_treated_as_secrets(self):
        from backend.utils.settings_utils import is_secret_key
        for key in (
            "claude_token_usage",
            "active_video_model",
            "allow_web_search",
            "keyboard_layout",
            "",
        ):
            assert not is_secret_key(key), key

    def test_dbapi_error_text_carries_the_value_it_failed_to_write(self):
        """The premise: SQLAlchemy stringifies bound parameters into the message."""
        from sqlalchemy.exc import DBAPIError
        exc = DBAPIError.instance(
            "UPDATE settings SET value=%(value)s WHERE key=%(key)s",
            {"value": "sk-live-SUPERSECRET", "key": "address_provider_key"},
            Exception("connection reset"),
            Exception,
        )
        assert "sk-live-SUPERSECRET" in str(exc)

    def test_redaction_withholds_the_message_for_secret_keys(self):
        from sqlalchemy.exc import DBAPIError
        from backend.utils.settings_utils import redact_exception
        exc = DBAPIError.instance(
            "UPDATE settings SET value=%(value)s WHERE key=%(key)s",
            {"value": "sk-live-SUPERSECRET", "key": "address_provider_key"},
            Exception("connection reset"),
            Exception,
        )
        text = redact_exception(exc, "address_provider_key")
        assert "sk-live-SUPERSECRET" not in text
        assert "DBAPIError" in text

    def test_redaction_keeps_the_message_for_ordinary_keys(self):
        from backend.utils.settings_utils import redact_exception
        text = redact_exception(RuntimeError("connection reset"), "active_video_model")
        assert text == "connection reset"

    def test_one_secret_key_redacts_a_multi_key_write(self):
        from backend.utils.settings_utils import redact_exception
        text = redact_exception(RuntimeError("boom"), "address_provider", "address_provider_key")
        assert "boom" not in text

    def test_save_setting_failure_does_not_log_the_secret(self, caplog):
        import logging
        from unittest.mock import MagicMock, patch
        from sqlalchemy.exc import DBAPIError
        from backend.utils.settings_utils import save_setting

        exc = DBAPIError.instance(
            "UPDATE settings SET value=%(value)s WHERE key=%(key)s",
            {"value": "sk-live-SUPERSECRET", "key": "address_provider_key"},
            Exception("connection reset"),
            Exception,
        )
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.side_effect = exc
                with caplog.at_level(logging.ERROR, logger="backend.utils.settings_utils"):
                    save_setting("address_provider_key", "sk-live-SUPERSECRET")

        assert caplog.text, "the failure should still be reported"
        assert "sk-live-SUPERSECRET" not in caplog.text
        assert "address_provider_key" in caplog.text

    def test_get_setting_failure_does_not_log_the_secret(self, caplog):
        import logging
        from unittest.mock import patch
        from sqlalchemy.exc import DBAPIError
        from backend.utils.settings_utils import get_setting

        exc = DBAPIError.instance(
            "INSERT INTO settings (key, value) VALUES (%(key)s, %(value)s)",
            {"value": "sk-live-SUPERSECRET", "key": "address_provider_key"},
            Exception("duplicate key"),
            Exception,
        )
        with patch("backend.utils.settings_utils.has_app_context", return_value=True):
            with patch("backend.utils.settings_utils.db") as mock_db:
                mock_db.session.get.side_effect = exc
                with caplog.at_level(logging.WARNING, logger="backend.utils.settings_utils"):
                    result = get_setting("address_provider_key", default=None)

        assert result is None
        assert "sk-live-SUPERSECRET" not in caplog.text
