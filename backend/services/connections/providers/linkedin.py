"""LinkedIn member posts through the official Posts API.

Each person registers their own LinkedIn app (self-serve products "Share on
LinkedIn" and "Sign In with LinkedIn using OpenID Connect") and authorizes it
with the OAuth authorization-code flow. The member URN comes from the OIDC
userinfo ``sub``.

Posts are text, with a link written into the text. The Posts API does not
fetch a link's preview ("Posts API does not support URL scraping"), so an
article card would carry only what we send; and an image needs the Images API,
whose upload method the docs give two ways and whose processing state a
``w_member_social`` token cannot read. Both are left out until they can be
checked against a live account.

LinkedIn's API Terms forbid using the API "to automate posting on the LinkedIn
Services" (section 3.1, item 26), so a LinkedIn post goes out only when a person
approves it, never on Guaardvark's timer.

Member access tokens last 60 days. LinkedIn issues refresh tokens only to
approved Marketing Developer Platform partners, so when a token expires the
connection is marked expired and the person authorizes again (which LinkedIn's
API Terms also require: consent is obtained again when a token expires).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urlencode

import requests

from backend.services.connections.base import (
    AUTH_OAUTH2,
    BOT_LABEL_NONE,
    DISCLOSE_NEVER,
    DISCLOSURE_TEXT,
    FAMILY_SOCIAL,
    STATUS_EXPIRED,
    AIDisclosure,
    BotLabel,
    Capabilities,
    ConnCtx,
    CredentialField,
    DeleteRequest,
    DeleteResult,
    ProgressFn,
    ProviderSpec,
    PublishRequest,
    PublishResult,
    RateLimit,
)

logger = logging.getLogger(__name__)

TIMEOUT = 60
AUTH_URI = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URI = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
SCOPES = ("openid", "profile", "w_member_social")

# Versioned API release sent as the LinkedIn-Version header (YYYYMM). Read
# 2026-10-16 at https://learn.microsoft.com/en-us/linkedin/marketing/versioning:
# 202610 is the latest release; versions are supported for at least a year, and
# 202610 until 2027-10-15. Move it forward before then.
API_VERSION = "202610"

# "The maximum length of the text of a UGC Post is 3,000 characters."
# https://learn.microsoft.com/en-us/linkedin/compliance/integrations/shares/ugc-post-api
# (read 2026-10-16; the Posts API schema states no figure of its own).
MAX_TEXT_CHARS = 3000
# Treated as expired this long before LinkedIn's own expiry, so a publish does
# not start with a token that lapses mid-request.
EXPIRY_MARGIN_SECONDS = 60

RATE_LIMITS_URL = "https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin"
API_TERMS_URL = "https://www.linkedin.com/legal/l/api-terms-of-use"

EXPIRED_MESSAGE = (
    "LinkedIn's authorization for this account has expired. LinkedIn member tokens "
    "last 60 days and LinkedIn gives refresh tokens only to approved partners, so "
    "open Edit and press Authorize with LinkedIn again."
)
SCHEDULE_REFUSAL = (
    "LinkedIn's API Terms forbid using the API to automate posting, so a LinkedIn "
    "post goes out when you approve it, not at a scheduled time. Publish it without a time."
)

SPEC = ProviderSpec(
    provider="linkedin",
    family=FAMILY_SOCIAL,
    label="LinkedIn",
    auth_kinds=(AUTH_OAUTH2,),
    credential_fields=(
        CredentialField(
            name="client_id",
            label="Client ID",
            help="From the Auth tab of your app in the LinkedIn Developer Portal.",
        ),
        CredentialField(
            name="client_secret",
            label="Primary Client Secret",
            help="From the same Auth tab.",
        ),
        CredentialField(
            name="access_token",
            label="Access token",
            required=False,
            help="Filled in automatically once you complete the authorization step.",
        ),
    ),
    capabilities=Capabilities(
        text=True,
        max_text_chars=MAX_TEXT_CHARS,
        images=False,
        video=False,
        supports_link=True,
        # Posts API visibility for a member post: PUBLIC or CONNECTIONS.
        visibilities=("public", "connections"),
        default_visibility="public",
        rate_limits=(
            RateLimit(
                posts=10,
                window_seconds=24 * 3600,
                platform_limit=(
                    "150 API requests per member and 100,000 per app each day (UTC); "
                    "a post is one request"
                ),
                source=RATE_LIMITS_URL,
            ),
        ),
        ai_disclosure=AIDisclosure(
            required_when=DISCLOSE_NEVER,
            mechanism=DISCLOSURE_TEXT,
            rule=(
                "LinkedIn has no rule for AI-assisted text. Its Professional Community "
                "Policies require disclosing synthetic or manipulated media that shows a "
                "person saying or doing something they did not; this connection posts "
                "text and links only."
            ),
            source="https://www.linkedin.com/legal/professional-community-policies",
        ),
        bot_label=BotLabel(
            kind=BOT_LABEL_NONE,
            how=(
                "LinkedIn has no automation label. Its User Agreement forbids bots and "
                "unauthorized automation on member accounts, and its API Terms forbid using "
                "the API to automate posting: Guaardvark sends a LinkedIn post only when you "
                "approve that post."
            ),
            source=API_TERMS_URL,
        ),
        native_scheduling=None,
        schedule_refusal=SCHEDULE_REFUSAL,
        deletable=True,
    ),
    hint_field="client_secret",
    docs_url="https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api",
    setup_help=(
        "Register your own LinkedIn app with the Share on LinkedIn and OpenID Connect "
        "products, then authorize this connection."
    ),
    setup_steps=(
        "Open the LinkedIn Developer Portal at linkedin.com/developers/apps and choose "
        "Create app. LinkedIn asks for a LinkedIn Page to associate with the app; an admin "
        "of that Page verifies it.",
        "On the app's Products tab, add \"Share on LinkedIn\" and \"Sign In with LinkedIn "
        "using OpenID Connect\". Both are self-serve.",
        "On the Auth tab, under Authorized redirect URLs for your app, add exactly: "
        "{redirect_uri}",
        "Copy the Client ID and the Primary Client Secret from the Auth tab into the "
        "fields below and save.",
        "Press Authorize with LinkedIn, approve, then paste the address your browser lands "
        "on (or just the code in it).",
        "LinkedIn tokens last 60 days. When this account shows expired, authorize again.",
    ),
    notice=(
        "Posts go out from your own LinkedIn account through LinkedIn's official API, "
        "each one only after you approve it, and never on a schedule. Text and links only."
    ),
)


def _request(method: str, url: str, **kwargs):
    """One HTTPS call. The session never adds ~/.netrc logins, which requests
    would otherwise put in place of the bearer token."""
    from backend.utils.hosts import no_netrc_session

    with no_netrc_session() as session:
        return session.request(method, url, timeout=TIMEOUT, **kwargs)


class _Expired(RuntimeError):
    """The access token can no longer be used; only a new authorization helps."""


def authorize_url(ctx: ConnCtx, redirect_uri: str, state: str) -> str:
    client_id = (ctx.secrets.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("No LinkedIn Client ID configured. Save it first.")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def _token_values(payload: Dict, now: float) -> Dict[str, str]:
    """What the store keeps from a token response; expiries as epoch seconds."""
    values = {"access_token": payload.get("access_token") or ""}
    if payload.get("expires_in"):
        values["access_token_expires_at"] = str(int(now + int(payload["expires_in"])))
    # Present only for partners LinkedIn enabled programmatic refresh for.
    if payload.get("refresh_token"):
        values["refresh_token"] = payload["refresh_token"]
        if payload.get("refresh_token_expires_in"):
            values["refresh_token_expires_at"] = str(
                int(now + int(payload["refresh_token_expires_in"]))
            )
    return values


def _error_detail(resp) -> str:
    try:
        body = resp.json() or {}
    except ValueError:
        return ""
    return str(body.get("error_description") or body.get("message") or body.get("error") or "")


def exchange_code(ctx: ConnCtx, code: str, redirect_uri: str) -> Dict[str, str]:
    resp = _request(
        "POST",
        TOKEN_URI,
        data={
            "grant_type": "authorization_code",
            "code": code.strip(),
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
            "redirect_uri": redirect_uri,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code >= 400:
        detail = _error_detail(resp)
        raise RuntimeError(
            f"LinkedIn refused the authorization code ({resp.status_code}"
            f"{f': {detail}' if detail else ''}). Codes last 30 minutes and work once; "
            "check the redirect URL matches the one registered on the app, then authorize again."
        )
    values = _token_values(resp.json() or {}, time.time())
    if not values["access_token"]:
        raise RuntimeError("LinkedIn returned no access token.")
    return values


def _refresh(ctx: ConnCtx, refresh_token: str) -> str:
    resp = _request(
        "POST",
        TOKEN_URI,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code in (400, 401):
        raise _Expired(EXPIRED_MESSAGE)
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn token refresh failed ({resp.status_code}).")
    values = _token_values(resp.json() or {}, time.time())
    if not values["access_token"]:
        raise RuntimeError("LinkedIn token refresh returned no access token.")
    ctx.save_secrets(values)
    return values["access_token"]


def _expires_at(ctx: ConnCtx, key: str) -> Optional[float]:
    try:
        return float(ctx.secrets.get(key) or 0) or None
    except ValueError:
        return None


def _access_token(ctx: ConnCtx) -> str:
    """The stored access token, refreshed where LinkedIn allows it.

    Raises ValueError before any authorization, _Expired when only a new one helps.
    """
    token = (ctx.secrets.get("access_token") or "").strip()
    expires_at = _expires_at(ctx, "access_token_expires_at")
    if token and (expires_at is None or expires_at - time.time() > EXPIRY_MARGIN_SECONDS):
        return token
    refresh = (ctx.secrets.get("refresh_token") or "").strip()
    refresh_expires = _expires_at(ctx, "refresh_token_expires_at")
    if refresh and (refresh_expires is None or refresh_expires > time.time()):
        return _refresh(ctx, refresh)
    if token:
        raise _Expired(EXPIRED_MESSAGE)
    raise ValueError("Not authorized yet: save the app's Client ID and secret, then press Authorize with LinkedIn.")


def _api_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _userinfo(token: str) -> Dict:
    resp = _request("GET", USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 401:
        raise _Expired(EXPIRED_MESSAGE)
    if resp.status_code == 403:
        raise RuntimeError(
            "LinkedIn refused the profile lookup (403). Add the \"Sign In with LinkedIn using "
            "OpenID Connect\" product to the app, then authorize again."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"LinkedIn returned {resp.status_code} for the profile lookup.")
    info = resp.json() or {}
    if not info.get("sub"):
        raise RuntimeError("LinkedIn's profile lookup returned no member id.")
    return info


def _member_urn(sub: str) -> str:
    return f"urn:li:person:{sub}"


def _token_expiry(ctx: ConnCtx) -> Optional[datetime]:
    expires_at = _expires_at(ctx, "access_token_expires_at")
    return datetime.fromtimestamp(expires_at) if expires_at else None


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        info = _userinfo(_access_token(ctx))
    except ValueError as e:
        return False, str(e), {}
    except _Expired as e:
        return False, str(e), {"expired": True}
    except RuntimeError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach LinkedIn: {e}", {}

    name = info.get("name") or " ".join(
        p for p in (info.get("given_name"), info.get("family_name")) if p
    ) or "LinkedIn member"
    discovered: Dict = {
        "handle": name,
        "display_name": name,
        "config": {"member_urn": _member_urn(info["sub"])},
    }
    expiry = _token_expiry(ctx)
    if expiry:
        discovered["token_expires_at"] = expiry
    until = f" Authorization lasts until {expiry:%Y-%m-%d}." if expiry else ""
    return True, f"Connected as {name}.{until}", discovered


# "All reserved characters need to be escaped with a backslash, even if those
# characters are not used in one of the supported elements or templates."
# https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/little-text-format
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_URL = re.compile(r"https?://\S+")
# A hashtag element: '#' then a word that starts with a letter, not inside a word.
_HASHTAG = re.compile(r"(?<![\w\\])\\#(?=[^\W\d_])")


def commentary(text: str) -> str:
    """``text`` as Posts API commentary ("little" text format).

    Every reserved character is escaped, which is what keeps a parenthesis or
    an underscore from being read as markup. Hashtags outside links stay
    hashtags; inside a link nothing is special.
    """
    out = []
    last = 0
    for match in _URL.finditer(text or ""):
        out.append(_HASHTAG.sub("#", _RESERVED.sub(r"\\\1", text[last:match.start()])))
        out.append(_RESERVED.sub(r"\\\1", match.group(0)))
        last = match.end()
    out.append(_HASHTAG.sub("#", _RESERVED.sub(r"\\\1", (text or "")[last:])))
    return "".join(out)


def _post_body(author: str, req: PublishRequest) -> Dict:
    text = req.body or ""
    if req.link_url and req.link_url not in text:
        text = f"{text}\n\n{req.link_url}".strip()
    return {
        "author": author,
        "commentary": commentary(text),
        "visibility": "CONNECTIONS" if (req.visibility or "") == "connections" else "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }


def _refused(resp, action: str) -> str:
    detail = _error_detail(resp)
    suffix = f": {detail}" if detail else ""
    if resp.status_code == 403:
        return (
            f"LinkedIn refused to {action} (403{suffix}). The app needs the Share on LinkedIn "
            "product, and the authorization must include w_member_social."
        )
    if resp.status_code == 429:
        return (
            f"LinkedIn's daily request limit for this member or app is used up (429{suffix}). "
            "It resets at midnight UTC."
        )
    return f"LinkedIn returned {resp.status_code} when asked to {action}{suffix}."


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    if req.media:
        return PublishResult(ok=False, message="LinkedIn posts from Guaardvark are text and links only.")
    try:
        on_progress(15, "Authenticating…")
        token = _access_token(ctx)
        author = (ctx.config.get("member_urn") or "").strip()
        if not author:
            author = _member_urn(_userinfo(token)["sub"])
        on_progress(60, "Posting…")
        resp = _request("POST", POSTS_URL, headers=_api_headers(token), json=_post_body(author, req))
    except ValueError as e:
        return PublishResult(ok=False, message=str(e))
    except _Expired as e:
        ctx.set_status(STATUS_EXPIRED, str(e))
        return PublishResult(ok=False, message=str(e))
    except RuntimeError as e:
        return PublishResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach LinkedIn: {e}")

    if resp.status_code == 401:
        ctx.set_status(STATUS_EXPIRED, EXPIRED_MESSAGE)
        return PublishResult(ok=False, message=EXPIRED_MESSAGE)
    if resp.status_code >= 400:
        return PublishResult(ok=False, message=_refused(resp, "create the post"))

    # "the response header x-restli-id contains the Post ID"
    urn = resp.headers.get("x-restli-id") or ""
    on_progress(95, "Posted.")
    return PublishResult(
        ok=True,
        remote_id=urn or None,
        remote_url=f"https://www.linkedin.com/feed/update/{urn}/" if urn else None,
        message="Posted to LinkedIn." if urn else "Posted to LinkedIn; it returned no post id, so Delete on platform is unavailable.",
    )


def delete(ctx: ConnCtx, req: DeleteRequest) -> DeleteResult:
    urn = (req.remote_id or "").strip()
    if not urn.startswith("urn:li:"):
        return DeleteResult(ok=False, message="This record does not name a LinkedIn post.")
    try:
        token = _access_token(ctx)
        resp = _request(
            "DELETE",
            f"{POSTS_URL}/{quote(urn, safe='')}",
            headers={**_api_headers(token), "X-RestLi-Method": "DELETE"},
        )
    except ValueError as e:
        return DeleteResult(ok=False, message=str(e))
    except _Expired as e:
        ctx.set_status(STATUS_EXPIRED, str(e))
        return DeleteResult(ok=False, message=str(e))
    except RuntimeError as e:
        return DeleteResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return DeleteResult(ok=False, message=f"Could not reach LinkedIn: {e}")
    if resp.status_code == 401:
        ctx.set_status(STATUS_EXPIRED, EXPIRED_MESSAGE)
        return DeleteResult(ok=False, message=EXPIRED_MESSAGE)
    if resp.status_code == 404:
        return DeleteResult(ok=True, message="The post was already gone from LinkedIn.")
    if resp.status_code >= 400:
        return DeleteResult(ok=False, message=_refused(resp, "delete the post"))
    return DeleteResult(ok=True, message="Deleted from LinkedIn.")
