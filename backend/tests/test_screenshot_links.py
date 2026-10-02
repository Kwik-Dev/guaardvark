"""Agent screen captures are served by signed links.

GET /api/tools/screenshots/<path> answers a link signed for that path, this
machine while the install has no key, or the API key. Chat history is signed as
it is served. Real blueprints behind the real auth hook through Flask's test
client, a temporary outputs folder, secret and SQLite database; no backend, GPU
or network.
"""

import base64
import stat

import pytest
from flask import Flask

from backend.api.tools_api import _extract_and_save_screenshots, tools_bp
from backend.api.unified_chat_api import unified_chat_bp
from backend.models import LLMMessage, LLMSession, db
from backend.utils import auth_guard, screenshot_urls
from backend.utils.screenshot_urls import screenshot_url, sign_screenshot_urls

REMOTE = "192.0.2.10"  # TEST-NET-1: never one of this machine's addresses
LOCAL = "127.0.0.1"
PREFIX = "/api/tools/screenshots/"


@pytest.fixture
def secret_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / ".screenshot_url_secret"
    monkeypatch.setattr(screenshot_urls, "secret_path", lambda storage_dir=None: path)
    monkeypatch.setattr(screenshot_urls, "_cached", None)
    return path


@pytest.fixture
def app(tmp_path, secret_file, monkeypatch):
    out = tmp_path / "outputs"
    (out / "screenshots").mkdir(parents=True)
    (out / "screenshots" / "agent_capture_1.webp").write_bytes(b"one")
    (out / "screenshots" / "agent_capture_2.webp").write_bytes(b"two")
    monkeypatch.setattr(auth_guard, "_local_ips_cache", {"127.0.0.1", "::1", "localhost"})
    monkeypatch.delenv("GUAARDVARK_API_KEY", raising=False)
    flask_app = Flask(__name__)
    flask_app.config.update(
        OUTPUT_DIR=str(out),
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(flask_app)
    flask_app.before_request(auth_guard.check_endpoint_auth)
    flask_app.register_blueprint(tools_bp)
    flask_app.register_blueprint(unified_chat_bp)
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _get(client, url, addr, headers=None):
    return client.get(url, environ_base={"REMOTE_ADDR": addr}, headers=headers or {})


def _sig(url):
    return url.split("sig=", 1)[1]


def test_a_signed_link_opens_from_another_host(client):
    url = screenshot_url("agent_capture_1.webp")
    assert url.startswith(PREFIX + "agent_capture_1.webp?sig=")
    response = _get(client, url, REMOTE)
    assert response.status_code == 200 and response.data == b"one"


def test_a_tampered_signature_is_refused(client):
    sig = _sig(screenshot_url("agent_capture_1.webp"))
    tampered = ("A" if sig[0] != "A" else "B") + sig[1:]
    refused = _get(client, f"{PREFIX}agent_capture_1.webp?sig={tampered}", REMOTE)
    assert refused.status_code == 403
    assert refused.get_json()["code"] == auth_guard.SCREENSHOT_LINK_CODE


def test_a_signature_is_good_for_its_own_file_only(client):
    sig = _sig(screenshot_url("agent_capture_1.webp"))
    assert _get(client, f"{PREFIX}agent_capture_2.webp?sig={sig}", REMOTE).status_code == 403


def test_another_host_cannot_tell_whether_a_file_exists(client):
    present = _get(client, f"{PREFIX}agent_capture_1.webp", REMOTE)
    missing = _get(client, f"{PREFIX}agent_capture_9.webp", REMOTE)
    wrong = _get(client, f"{PREFIX}agent_capture_9.webp?sig=nope", REMOTE)
    assert present.status_code == missing.status_code == wrong.status_code == 403
    assert present.get_json() == missing.get_json() == wrong.get_json()


def test_this_machine_needs_no_signature_while_there_is_no_key(client):
    assert _get(client, f"{PREFIX}agent_capture_1.webp", LOCAL).status_code == 200


def test_with_a_key_the_key_or_a_signature_opens_it(client, monkeypatch):
    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    plain = f"{PREFIX}agent_capture_1.webp"
    assert _get(client, plain, REMOTE, {"X-API-Key": "k-test"}).status_code == 200
    assert _get(client, plain, LOCAL).status_code == 403
    assert _get(client, screenshot_url("agent_capture_1.webp"), LOCAL).status_code == 200


def test_a_signed_in_browser_needs_no_signature(client, monkeypatch):
    from backend.utils import api_session

    monkeypatch.setenv("GUAARDVARK_API_KEY", "k-test")
    plain = f"{PREFIX}agent_capture_1.webp"
    assert _get(client, plain, REMOTE).status_code == 403
    client.set_cookie(api_session.cookie_name(), api_session.session_token("k-test"))
    assert _get(client, plain, REMOTE).data == b"one"
    client.set_cookie(api_session.cookie_name(), api_session.session_token("an-old-key"))
    assert _get(client, plain, REMOTE).status_code == 403


def test_the_secret_is_private_and_deleting_it_revokes_every_link(client, secret_file):
    old = screenshot_url("agent_capture_1.webp")
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    secret_file.unlink()
    assert _get(client, old, REMOTE).status_code == 403
    assert _get(client, screenshot_url("agent_capture_1.webp"), REMOTE).status_code == 200


def test_route_and_execute_hands_out_signed_links(app, client):
    png = base64.b64encode(b"\x89PNG").decode()
    result = {"steps": [{"observations": [{"result": {"metadata": {"image_base64": png, "format": "png"}}}]}]}
    with app.test_request_context():
        urls = _extract_and_save_screenshots(result)
    assert len(urls) == 1 and "?sig=" in urls[0]
    assert _get(client, urls[0], REMOTE).data == b"\x89PNG"


def test_signing_text_replaces_old_signatures_and_leaves_other_links():
    value = {
        "content": f"Done.\n\n![Screenshot]({PREFIX}agent_capture_1.webp)",
        "images": [{"url": f"{PREFIX}agent_capture_2.webp?sig=stale"}, {"url": "/api/outputs/generated_images/x.png"}],
        "count": 2,
    }
    signed = sign_screenshot_urls(value)
    assert signed["content"].endswith(f"{screenshot_url('agent_capture_1.webp')})")
    assert signed["images"][0]["url"] == screenshot_url("agent_capture_2.webp")
    assert signed["images"][1]["url"] == "/api/outputs/generated_images/x.png"
    assert signed["count"] == 2
    assert value["images"][0]["url"].endswith("sig=stale")  # the input is not changed


def _seed_history():
    db.session.add(LLMSession(id="s1", user="default"))
    db.session.flush()
    db.session.add(LLMMessage(
        session_id="s1", role="assistant",
        content=f"Done.\n\n![Screenshot]({PREFIX}agent_capture_1.webp)",
        extra_data={"generatedImages": [{"url": f"{PREFIX}agent_capture_2.webp?sig=stale", "alt": "capture"}]},
    ))
    db.session.commit()


def _link_in(markdown):
    return markdown.split("](", 1)[1].rstrip(")")


def test_unified_chat_history_is_served_with_signed_links(client):
    _seed_history()
    message = _get(client, "/api/chat/unified/s1/history", REMOTE).get_json()["messages"][0]
    assert _get(client, _link_in(message["content"]), REMOTE).data == b"one"
    assert _get(client, message["extra_data"]["generatedImages"][0]["url"], REMOTE).data == b"two"
    # Signed on the way out; the saved row is untouched.
    row = db.session.get(LLMMessage, 1)
    assert "sig=" not in row.content


def test_enhanced_chat_history_is_served_with_signed_links(app, client):
    try:
        from backend.api.enhanced_chat_api import enhanced_chat_bp
    except Exception as exc:  # pragma: no cover - heavy optional imports
        pytest.skip(f"enhanced chat API not importable here: {exc}")
    app.register_blueprint(enhanced_chat_bp)
    _seed_history()
    message = _get(client, "/api/enhanced-chat/s1/history", REMOTE).get_json()["messages"][0]
    assert _get(client, _link_in(message["content"]), REMOTE).data == b"one"
    assert _get(client, message["generatedImages"][0]["url"], REMOTE).data == b"two"
