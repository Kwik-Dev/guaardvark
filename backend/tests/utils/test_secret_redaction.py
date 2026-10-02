"""The table behind backend/utils/secret_redaction.py.

One row per shape: the text as a log would carry it, the secret that must not
survive, and the text that must come out. A new pattern in SECRET_PATTERNS
gets a row in MASKED; a false positive found in real logs gets a row in
UNTOUCHED.
"""

from __future__ import annotations

import pytest

from backend.utils.secret_redaction import SECRET_PATTERNS, redact_fields, redact_secrets

# Secrets are assembled from pieces so no scanner mistakes this file for a leak.
_HF = "hf_" + "AbCdEfGhIjKlMnOpQrStUvWx12"
_SK = "sk-" + "live_0123456789abcdefghijKLMN"
_GH = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyzAB"
_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTY3ODkwIn0" + ".SflKxwRJSMeKKF2QT4fwpM"
_PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"
_DISCORD_BOT = "M" + "T" * 23 + "." + "AbCdEf" + "." + "z" * 27

# (pattern name, input, the secret, expected output)
MASKED = [
    ("url_password",
     "INFO app : Using database URL: postgresql://guaardvark:S3cr3t-Pw_28chars@localhost:5432/guaardvark",
     "S3cr3t-Pw_28chars",
     "INFO app : Using database URL: postgresql://guaardvark:***@localhost:5432/guaardvark"),
    ("url_password", "broker redis://:r3d1sPass@127.0.0.1:6379/0 unreachable", "r3d1sPass",
     "broker redis://:***@127.0.0.1:6379/0 unreachable"),
    ("url_password", "clone https://deploy:tok3nValue@git.example.com/x.git failed", "tok3nValue",
     "clone https://deploy:***@git.example.com/x.git failed"),
    ("authorization_header", "headers={'Authorization': 'Bearer abc.def-123456'}", "abc.def-123456",
     "headers={'Authorization': 'Bearer ***'}"),
    ("authorization_header", "> Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA==",
     "> Authorization: Basic ***"),
    ("authorization_header", "Proxy-Authorization: s3cretvalue99", "s3cretvalue99", "Proxy-Authorization: ***"),
    ("bearer_token", "sent Bearer abcdefgh12345678 to upstream", "abcdefgh12345678", "sent Bearer *** to upstream"),
    ("cookie_header", "Cookie: session=abc123; theme=dark", "abc123", "Cookie: ***"),
    ("cookie_header", "Set-Cookie: sid=zzz999; HttpOnly", "zzz999", "Set-Cookie: ***"),
    ("named_secret", "connect password=hunter2hunter2 host=db", "hunter2hunter2", "connect password=*** host=db"),
    ("named_secret", 'payload {"api_key": "k-123456789", "model": "x"}', "k-123456789",
     'payload {"api_key": ***, "model": "x"}'),
    ("named_secret", "env GUAARDVARK_API_KEY=abcd1234efgh PORT=5000", "abcd1234efgh",
     "env GUAARDVARK_API_KEY=*** PORT=5000"),
    ("named_secret", "HF_TOKEN: plainvalue123", "plainvalue123", "HF_TOKEN: ***"),
    ("named_secret", "X-API-Key: 9f8e7d6c5b4a", "9f8e7d6c5b4a", "X-API-Key: ***"),
    ("named_secret", "client_secret='with spaces inside' next", "with spaces inside", "client_secret=*** next"),
    ("named_secret", "POSTGRES_PASSWORD=pg-pass-1 started", "pg-pass-1", "POSTGRES_PASSWORD=*** started"),
    ("named_secret", "GET /v1/x?api_key=FAKEKEY_abcdef123&page=2 HTTP/1.1", "FAKEKEY_abcdef123",
     "GET /v1/x?api_key=***&page=2 HTTP/1.1"),
    ("url_query_secret", "GET https://maps.example.com/api?key=AbCdEf123456&q=1", "AbCdEf123456",
     "GET https://maps.example.com/api?key=***&q=1"),
    ("discord_webhook", "POST https://discord.com/api/webhooks/123456789012345678/AbCdEfGhIjKlMnOpQrStUvWxYz-_0123",
     "AbCdEfGhIjKlMnOpQrStUvWxYz-_0123", "POST https://discord.com/api/webhooks/123456789012345678/***"),
    ("slack_webhook", "hook https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXX", "T000/B000/XXXXXXXXXXXXXXXX",
     "hook https://hooks.slack.com/services/***"),
    ("huggingface_token", f"download failed for {_HF}", _HF, "download failed for ***"),
    ("sk_api_key", f"[parameters: {{'value': '{_SK}'}}]", _SK, "[parameters: {'value': '***'}]"),
    ("github_token", f"remote: {_GH} rejected", _GH, "remote: *** rejected"),
    ("slack_token", "slack xoxb-1234567890-abcdefghij ok", "xoxb-1234567890-abcdefghij", "slack *** ok"),
    ("aws_access_key_id", "aws AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE", "aws *** used"),
    ("google_api_key", "maps AIza" + "SyA1234567890abcdefghijklmnopqrstuv done",
     "SyA1234567890abcdefghijklmnopqrstuv", "maps *** done"),
    ("discord_bot_token", f"login with {_DISCORD_BOT} failed", _DISCORD_BOT, "login with *** failed"),
    ("telegram_bot_token", "bot 123456789:" + "A" * 35 + " started", "A" * 35, "bot *** started"),
    ("jwt", f"id_token {_JWT} issued", _JWT, "id_token *** issued"),
    ("private_key_block",
     f"key:\n-----BEGIN RSA PRIVATE KEY-----\n{_PEM_BODY}\n-----END RSA PRIVATE KEY-----\nnext line",
     _PEM_BODY, "key:\n<private key ***>\nnext line"),
    ("private_key_block", f"-----BEGIN PRIVATE KEY-----\n{_PEM_BODY}", _PEM_BODY, "<private key ***>"),
    ("private_key_tail", f"{_PEM_BODY}\n-----END PRIVATE KEY-----\nnext line", _PEM_BODY,
     "<private key ***>\nnext line"),
]

# Lines that look a little like the above and must come back unchanged.
UNTOUCHED = [
    "2026-09-30 00:00:01,000 INFO app [PID:1 TID:1] : listening on http://127.0.0.1:5000/api",
    "connected to redis://localhost:6379/0 and ws://localhost:9222/session",
    "usage prompt_tokens=35 completion_tokens=120 max_tokens=2048 token_usage=155",
    "tokens: 512, token_count: 9, secrets_found: 0",
    "api_key=None password='' token=null secret=<redacted>",
    "password=*** api_key: ***",
    "session_id=5f2c9a chat session resumed, cache_key=thumb:42 sort key: created_at",
    "Authorization failed: the API key is missing",
    "Loaded 12 cookies from the browser profile",
    "task-20260930-abcdefghij1234567890 finished; risk-assessment-of-something-long-enough done",
    "git@github.com:guaardvark/guaardvark.git fetched at 12:30:45",
    "File \"backend/app.py\", line 502, in create_app",
    "hf_hub_download(repo_id='org/model', filename='model.safetensors')",
]


@pytest.mark.parametrize("name, text, secret, expected", MASKED, ids=[f"{i}-{row[0]}" for i, row in enumerate(MASKED)])
def test_secret_is_masked_and_the_rest_is_kept(name, text, secret, expected):
    out = redact_secrets(text)

    assert secret not in out
    assert out == expected


@pytest.mark.parametrize("text", UNTOUCHED)
def test_ordinary_log_text_is_left_alone(text):
    assert redact_secrets(text) == text


def test_every_pattern_has_a_row_in_the_table():
    covered = {row[0] for row in MASKED}

    assert {pattern.name for pattern in SECRET_PATTERNS} <= covered


def test_redaction_is_stable_when_applied_twice():
    for _, text, _, expected in MASKED:
        assert redact_secrets(expected) == expected


def test_a_plugin_reply_keeps_its_status_and_loses_its_credentials():
    token = "Zm9vYmFyYmF6cXV4" * 2
    reply = {
        "status": "healthy",
        "auth_token": token,
        "token": token,
        "gpu": {"name": "GPU", "vram_total_mb": 16000, "api_key": token},
        "backends": [{"name": "kokoro", "password": token, "loaded": True}],
        "max_tokens": 4096,
        "total_tokens": 12,
        "token_count": 3,
        "secret_count": 0,
        "database": "postgresql://guaardvark:" + token + "@localhost/guaardvark",
        "empty_token": "",
        "unset_api_key": None,
    }
    out = redact_fields(reply)

    assert token not in repr(out)
    assert out["status"] == "healthy" and out["gpu"]["vram_total_mb"] == 16000
    assert out["auth_token"] == out["token"] == out["gpu"]["api_key"] == out["backends"][0]["password"] == "***"
    assert (out["max_tokens"], out["total_tokens"], out["token_count"], out["secret_count"]) == (4096, 12, 3, 0)
    assert out["database"] == "postgresql://guaardvark:***@localhost/guaardvark"
    assert out["empty_token"] == "" and out["unset_api_key"] is None
    assert reply["auth_token"] == token
