"""Bluesky publishing over AT Protocol XRPC.

Authentication uses an app password rather than the account password; sessions
are short-lived so one is created per operation instead of being cached.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests

from backend.services.connections.base import (
    AUTH_APP_PASSWORD,
    BOT_LABEL_ACCOUNT_SETTING,
    FAMILY_SOCIAL,
    BotLabel,
    Capabilities,
    ConfigField,
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

TIMEOUT = 120
DEFAULT_PDS = "https://bsky.social"
BLOB_LIMIT = 976_560
POST_LIMIT = 300
POST_COLLECTION = "app.bsky.feed.post"
RATE_LIMITS_URL = "https://docs.bsky.app/docs/advanced-guides/rate-limits"

SPEC = ProviderSpec(
    provider="bluesky",
    family=FAMILY_SOCIAL,
    label="Bluesky",
    auth_kinds=(AUTH_APP_PASSWORD,),
    credential_fields=(
        CredentialField(
            name="identifier",
            label="Handle",
            help="Your full handle, e.g. someone.bsky.social",
            placeholder="someone.bsky.social",
        ),
        CredentialField(
            name="app_password",
            label="App password",
            help="Settings → Privacy and security → App passwords. Not your login password.",
            placeholder="xxxx-xxxx-xxxx-xxxx",
        ),
    ),
    config_fields=(
        ConfigField(
            name="pds_url",
            label="PDS URL",
            default=DEFAULT_PDS,
            help="Change only if you self-host your data server.",
        ),
    ),
    capabilities=Capabilities(
        max_text_chars=POST_LIMIT,
        images=True,
        max_images=4,
        max_image_bytes=BLOB_LIMIT,
        video=False,
        visibilities=("public",),
        default_visibility="public",
        accepted_mime=("image/jpeg", "image/png", "image/webp", "image/gif"),
        # A post is one record create, which costs 3 write points.
        rate_limits=(
            RateLimit(
                posts=10,
                window_seconds=3600,
                platform_limit="5,000 write points per hour per account (about 1,666 posts)",
                source=RATE_LIMITS_URL,
            ),
            RateLimit(
                posts=100,
                window_seconds=24 * 3600,
                platform_limit="35,000 write points per day per account (about 11,666 posts)",
                source=RATE_LIMITS_URL,
            ),
        ),
        bot_label=BotLabel(
            kind=BOT_LABEL_ACCOUNT_SETTING,
            how=(
                "Bluesky's guide for bots asks automated accounts to label themselves as "
                "bots and to reply only when tagged. Guaardvark does not read or set that label."
            ),
            source="https://docs.bsky.app/docs/starter-templates/bots",
        ),
        deletable=True,
    ),
    hint_field="app_password",
    docs_url="https://docs.bsky.app/docs/advanced-guides/posting",
    setup_help="Create an app password in Bluesky settings — never use your account password.",
)


def _pds(ctx: ConnCtx) -> str:
    url = (ctx.config.get("pds_url") or DEFAULT_PDS).strip().rstrip("/")
    return url if url.startswith(("http://", "https://")) else f"https://{url}"


def _create_session(ctx: ConnCtx) -> Tuple[str, str]:
    """Return ``(access_jwt, did)``."""
    identifier = (ctx.secrets.get("identifier") or "").strip()
    password = (ctx.secrets.get("app_password") or "").strip()
    if not identifier or not password:
        raise ValueError("Handle and app password are both required.")

    resp = requests.post(
        f"{_pds(ctx)}/xrpc/com.atproto.server.createSession",
        json={"identifier": identifier, "password": password},
        timeout=TIMEOUT,
    )
    if resp.status_code == 401:
        raise RuntimeError("Bluesky rejected the handle or app password.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Bluesky returned {resp.status_code} creating a session.")
    session = resp.json()
    return session["accessJwt"], session["did"]


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        _, did = _create_session(ctx)
    except ValueError as e:
        return False, str(e), {}
    except RuntimeError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach Bluesky: {e}", {}

    handle = (ctx.secrets.get("identifier") or "").strip()
    return True, f"Connected as @{handle}.", {
        "handle": f"@{handle}",
        "display_name": handle,
        "config": {"did": did},
    }


def _upload_blob(ctx: ConnCtx, jwt: str, item) -> Dict:
    if item.bytes > BLOB_LIMIT:
        raise RuntimeError(
            f"{item.path} exceeds the {BLOB_LIMIT} byte blob limit; resize it first."
        )
    with open(item.path, "rb") as handle:
        resp = requests.post(
            f"{_pds(ctx)}/xrpc/com.atproto.repo.uploadBlob",
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": item.mime},
            data=handle.read(),
            timeout=TIMEOUT,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Blob upload failed ({resp.status_code}).")
    return resp.json()["blob"]


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    try:
        on_progress(15, "Authenticating…")
        jwt, did = _create_session(ctx)

        images: List[Dict] = []
        for index, item in enumerate(req.media):
            on_progress(25 + int(45 * index / max(len(req.media), 1)), "Uploading image…")
            images.append(
                {"alt": item.alt_text or "", "image": _upload_blob(ctx, jwt, item)}
            )
    except ValueError as e:
        return PublishResult(ok=False, message=str(e))
    except (OSError, RuntimeError) as e:
        return PublishResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach Bluesky: {e}")

    text = req.body or ""
    if req.link_url:
        text = f"{text}\n{req.link_url}".strip()

    record = {
        "$type": "app.bsky.feed.post",
        "text": text[:POST_LIMIT],
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if images:
        record["embed"] = {"$type": "app.bsky.embed.images", "images": images}

    on_progress(80, "Posting…")
    try:
        resp = requests.post(
            f"{_pds(ctx)}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"repo": did, "collection": POST_COLLECTION, "record": record},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach Bluesky: {e}")

    if resp.status_code >= 400:
        return PublishResult(ok=False, message=f"Bluesky returned {resp.status_code}.")

    created = resp.json()
    uri = created.get("uri") or ""
    handle = (ctx.secrets.get("identifier") or "").strip()
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return PublishResult(
        ok=True,
        remote_id=uri,
        remote_url=f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None,
        message="Posted to Bluesky.",
    )


def delete(ctx: ConnCtx, req: DeleteRequest) -> DeleteResult:
    """Delete the post record named by its at:// URI."""
    # at://<did>/app.bsky.feed.post/<rkey>
    parts = (req.remote_id or "").removeprefix("at://").split("/")
    if len(parts) != 3 or parts[1] != POST_COLLECTION:
        return DeleteResult(ok=False, message="This record does not name a Bluesky post.")
    repo, _, rkey = parts
    try:
        jwt, did = _create_session(ctx)
        if did != repo:
            return DeleteResult(ok=False, message="This post belongs to another Bluesky account.")
        resp = requests.post(
            f"{_pds(ctx)}/xrpc/com.atproto.repo.deleteRecord",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"repo": did, "collection": POST_COLLECTION, "rkey": rkey},
            timeout=TIMEOUT,
        )
    except (ValueError, RuntimeError) as e:
        return DeleteResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return DeleteResult(ok=False, message=f"Could not reach Bluesky: {e}")
    if resp.status_code >= 400:
        return DeleteResult(ok=False, message=f"Bluesky returned {resp.status_code}.")
    return DeleteResult(ok=True, message="Deleted from Bluesky.")
