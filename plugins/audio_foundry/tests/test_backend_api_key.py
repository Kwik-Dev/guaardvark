"""Which API key the plugin sends to the backend's protected routes."""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from service import config_loader  # noqa: E402


def _env_file(tmp_path, monkeypatch, text):
    env = tmp_path / ".env"
    env.write_text(text)
    monkeypatch.setattr(config_loader, "CHECKOUT_ENV", env)


def test_own_backend_reads_the_checkout_key_first(tmp_path, monkeypatch):
    _env_file(tmp_path, monkeypatch, "FLASK_PORT=5000\nGUAARDVARK_API_KEY=old\nGUAARDVARK_API_KEY='saved'\n")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "from-env")
    assert config_loader.backend_api_key(own_backend=True) == "saved"


def test_own_backend_falls_back_to_the_environment(tmp_path, monkeypatch):
    _env_file(tmp_path, monkeypatch, "FLASK_PORT=5000\n")
    monkeypatch.setenv("GUAARDVARK_API_KEY", "from-env")
    assert config_loader.backend_api_key(own_backend=True) == "from-env"


def test_other_backend_never_gets_the_checkout_key(tmp_path, monkeypatch):
    _env_file(tmp_path, monkeypatch, "GUAARDVARK_API_KEY=saved\n")
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    assert config_loader.backend_api_key(own_backend=False) == ""


def test_no_key_anywhere(tmp_path, monkeypatch):
    _env_file(tmp_path, monkeypatch, "")
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    assert config_loader.backend_api_key() == ""


def test_backend_url_is_own(monkeypatch):
    monkeypatch.delenv("GUAARDVARK_BACKEND_URL", raising=False)
    assert config_loader.backend_url_is_own(None)
    assert not config_loader.backend_url_is_own("http://other:5000")
    monkeypatch.setenv("GUAARDVARK_BACKEND_URL", "http://other:5000")
    assert not config_loader.backend_url_is_own(None)
