"""Mask credentials in text that is about to leave the machine.

Log lines, error bodies and command lines can carry a database password, an
API key or an Authorization header. ``redact_secrets`` replaces the secret
part with ``***`` and leaves the rest of the line readable, so the text is
still useful for diagnosis.

Every shape it knows is one row of ``SECRET_PATTERNS``; a new shape is a new
row plus a line in the table in ``backend/tests/utils/test_secret_redaction.py``.
The match is by shape or by label. A secret with neither (a bare random string
printed with no name next to it) is not recognised.
"""

from __future__ import annotations

import re
from typing import Callable, NamedTuple, Pattern, Union

REDACTED = "***"

# Values that say "no secret here"; masking them would hide the diagnosis
# ("api_key=None" is the bug report).
_EMPTY_VALUES = frozenset({
    "", "none", "null", "nil", "true", "false", "unset", "missing", "empty",
    "redacted", "<redacted>", "[redacted]", "<hidden>", "<none>", "...",
})

# The last word of a key whose value is a credential. Matched at the END of
# the key, so counters such as max_tokens, token_usage or secret_count stay.
_SECRET_KEY_END = (
    r"(?:passw(?:or)?d|passphrase|pwd|secret|token|credentials?|"
    r"(?:api|access|secret|private|auth|signing|encryption|license|master|client|bot|webhook)[_\-]?key|"
    r"apikey|client[_\-]?secret)"
)
_NAMED_SECRET = re.compile(
    r"(?P<key>[\"']?[A-Za-z0-9_.\-]*" + _SECRET_KEY_END + r"[\"']?)"
    r"(?P<sep>[ \t]*[:=][ \t]*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&)\]}\"']+)",
    re.IGNORECASE,
)


def _mask_named(match: "re.Match[str]") -> str:
    value = match.group("value")
    bare = value.strip("\"'").strip().lower()
    if bare in _EMPTY_VALUES or set(bare) <= {"*"} or set(bare) <= {"•"}:
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{REDACTED}"


class SecretPattern(NamedTuple):
    name: str
    regex: Pattern[str]
    replacement: Union[str, Callable[["re.Match[str]"], str]]


SECRET_PATTERNS = (
    # scheme://user:password@host, any scheme (postgresql, redis, amqp, https).
    SecretPattern(
        "url_password",
        re.compile(r"\b([a-z][a-z0-9+.\-]*://[^\s:/@]*):[^\s@/]+@", re.IGNORECASE),
        r"\1:" + REDACTED + "@",
    ),
    # Authorization: Bearer x / 'Authorization': 'Basic x' / Proxy-Authorization.
    SecretPattern(
        "authorization_header",
        re.compile(
            r"\b((?:proxy-)?authorization[\"']?[ \t]*[:=][ \t]*[\"']?(?:(?:bearer|basic|token|digest)[ \t]+)?)"
            r"[^\s\"',;]+",
            re.IGNORECASE,
        ),
        r"\1" + REDACTED,
    ),
    # A bearer token quoted without its header name.
    SecretPattern(
        "bearer_token",
        re.compile(r"\b(bearer[ \t]+)[A-Za-z0-9._~+/=\-]{8,}", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    # Cookie and Set-Cookie carry session secrets in a value of their own.
    SecretPattern(
        "cookie_header",
        re.compile(r"\b((?:set-)?cookie[\"']?[ \t]*[:=][ \t]*[\"']?)[^\"'\r\n]+", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    # password=..., "api_key": "...", HF_TOKEN=..., X-API-Key: ...
    SecretPattern("named_secret", _NAMED_SECRET, _mask_named),
    # ?key=... and friends, whose names are too short to trust outside a URL.
    SecretPattern(
        "url_query_secret",
        re.compile(r"([?&](?:key|sig|code|auth|access_token|id_token|refresh_token)=)[^&\s\"'#]+", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    # Webhook URLs are credentials: whoever holds one can post.
    SecretPattern(
        "discord_webhook",
        re.compile(r"(discord(?:app)?\.com/api/webhooks/\d+/)[\w\-]+", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    SecretPattern(
        "slack_webhook",
        re.compile(r"(hooks\.slack\.com/services/)[A-Za-z0-9/_\-]+", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
    # Provider tokens recognisable by their prefix, wherever they appear.
    SecretPattern("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}"), REDACTED),
    SecretPattern("sk_api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), REDACTED),
    SecretPattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
        REDACTED,
    ),
    SecretPattern("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), REDACTED),
    SecretPattern(
        "discord_bot_token",
        re.compile(r"\b[MNO][A-Za-z0-9_\-]{23,25}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
        REDACTED,
    ),
    SecretPattern("telegram_bot_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"), REDACTED),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        REDACTED,
    ),
    # A PEM private key, whole; or, when the text starts mid-key, up to its end.
    SecretPattern(
        "private_key_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        "<private key " + REDACTED + ">",
    ),
    SecretPattern(
        "private_key_tail",
        re.compile(r"\A(?:(?!-----BEGIN)[\s\S])*?-----END [A-Z ]*PRIVATE KEY-----"),
        "<private key " + REDACTED + ">",
    ),
)


def redact_secrets(text: str) -> str:
    """``text`` with every credential ``SECRET_PATTERNS`` recognises masked."""
    out = str(text)
    for pattern in SECRET_PATTERNS:
        out = pattern.regex.sub(pattern.replacement, out)
    return out


_SECRET_KEY = re.compile(r"[A-Za-z0-9_.\-]*" + _SECRET_KEY_END, re.IGNORECASE)


def _holds_a_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        bare = value.strip().lower()
        return bare not in _EMPTY_VALUES and not set(bare) <= {"*"} and not set(bare) <= {"•"}
    return True


def redact_fields(value):
    """A copy of a JSON-shaped value (a plugin's health or status reply, say)
    for a reply that leaves this process: the value under every key named
    like a credential (auth_token, api_key, password, ...) becomes REDACTED,
    and every other string goes through ``redact_secrets``, which catches a
    DSN's password or a bearer header inside a message."""
    if isinstance(value, dict):
        return {
            key: REDACTED if isinstance(key, str) and _SECRET_KEY.fullmatch(key) and _holds_a_value(item)
            else redact_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_fields(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value
