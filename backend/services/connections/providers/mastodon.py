"""Mastodon publishing over the REST API.

Media upload is asynchronous: v2 returns 202 while the server transcodes, and
the attachment cannot be referenced until it resolves, so uploads are polled
before the status is created.

A scheduled status is a separate object on the server until its time comes,
and the API does not link it to the status it becomes. So a post Mastodon
publishes on schedule can be deleted from here only while it is still waiting.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timezone
from typing import Dict, List, Optional, Tuple

import requests

from backend.services.connections.base import (
    AUTH_API_TOKEN,
    BOT_LABEL_ACCOUNT_SETTING,
    DISCLOSE_AI_ASSISTED,
    DISCLOSURE_TEXT,
    FAMILY_SOCIAL,
    SCHEDULE_PENDING,
    SCHEDULE_PUBLISHED,
    AIDisclosure,
    BotLabel,
    Capabilities,
    ConfigField,
    ConnCtx,
    CredentialField,
    DeleteRequest,
    DeleteResult,
    NativeScheduling,
    ProgressFn,
    ProviderSpec,
    PublishRequest,
    PublishResult,
    RateLimit,
    ScheduleCheck,
)

logger = logging.getLogger(__name__)

TIMEOUT = 120
MEDIA_POLL_ATTEMPTS = 30
MEDIA_POLL_INTERVAL = 2

SPEC = ProviderSpec(
    provider="mastodon",
    family=FAMILY_SOCIAL,
    label="Mastodon",
    auth_kinds=(AUTH_API_TOKEN,),
    credential_fields=(
        CredentialField(
            name="access_token",
            label="Access token",
            help="Preferences → Development → New application, with scopes read and write.",
        ),
    ),
    config_fields=(
        ConfigField(
            name="instance_url",
            label="Instance URL",
            required=True,
            default="https://mastodon.social",
            help="The server your account lives on.",
        ),
    ),
    capabilities=Capabilities(
        max_text_chars=500,
        images=True,
        max_images=4,
        max_image_bytes=16 * 1024 * 1024,
        video=True,
        max_video_bytes=99 * 1024 * 1024,
        supports_tags=True,
        visibilities=("public", "unlisted", "private", "direct"),
        default_visibility="unlisted",
        rate_limits=(
            # A post here may carry four media, so the media cap binds first.
            RateLimit(
                posts=4,
                window_seconds=30 * 60,
                platform_limit=(
                    "30 media uploads per 30 minutes and 300 API calls per 5 minutes, per account"
                ),
                source="https://docs.joinmastodon.org/api/rate-limits/",
            ),
        ),
        ai_disclosure=AIDisclosure(
            required_when=DISCLOSE_AI_ASSISTED,
            mechanism=DISCLOSURE_TEXT,
            rule=(
                "Server rules decide, and many require it: mastodon.social's rules say "
                "use of generative AI must be disclosed. Posts with generated media or "
                "text an agent drafted carry a disclosure line."
            ),
            source="https://mastodon.social/api/v1/instance/rules",
        ),
        bot_label=BotLabel(
            kind=BOT_LABEL_ACCOUNT_SETTING,
            how=(
                "Mark the account as a bot (automated account) in your server's profile "
                "settings; Mastodon then shows a bot badge on it. Test reads whether it is set."
            ),
            readable=True,
            api_field="bot",
            source="https://docs.joinmastodon.org/entities/Account/",
        ),
        native_scheduling=NativeScheduling(
            field="scheduled_at",
            min_lead_seconds=5 * 60,
            source="https://docs.joinmastodon.org/methods/statuses/",
        ),
        deletable=True,
    ),
    hint_field="access_token",
    docs_url="https://docs.joinmastodon.org/methods/statuses/",
    setup_help="Create an application on your instance and paste its access token.",
)


def _base(ctx: ConnCtx) -> str:
    url = (ctx.config.get("instance_url") or "").strip().rstrip("/")
    if not url:
        raise ValueError("No instance URL configured.")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def _headers(ctx: ConnCtx) -> Dict[str, str]:
    token = (ctx.secrets.get("access_token") or "").strip()
    if not token:
        raise ValueError("No access token configured.")
    return {"Authorization": f"Bearer {token}"}


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        resp = requests.get(
            f"{_base(ctx)}/api/v1/accounts/verify_credentials",
            headers=_headers(ctx),
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach the instance: {e}", {}

    if resp.status_code == 401:
        return False, "Token rejected — check it has read and write scopes.", {}
    if resp.status_code >= 400:
        return False, f"Instance returned {resp.status_code}.", {}

    account = resp.json()
    handle = account.get("acct") or account.get("username") or ""
    discovered: Dict = {
        "handle": f"@{handle}" if handle and not handle.startswith("@") else handle,
        "display_name": account.get("display_name") or handle,
        "config": {"bot_label_set": bool(account.get("bot"))},
    }

    # Instances configure their own character limit; prefer the live value.
    try:
        info = requests.get(f"{_base(ctx)}/api/v1/instance", timeout=TIMEOUT)
        if info.ok:
            limit = (info.json().get("configuration") or {}).get("statuses", {}).get(
                "max_characters"
            )
            if isinstance(limit, int) and limit > 0:
                discovered["capabilities"] = {"max_text_chars": limit}
    except requests.RequestException:
        pass

    return True, f"Connected as {discovered['handle']}.", discovered


def _upload_media(ctx: ConnCtx, item, on_progress: ProgressFn, index: int, total: int) -> str:
    base, headers = _base(ctx), _headers(ctx)
    on_progress(
        20 + int(50 * index / max(total, 1)),
        f"Uploading {os.path.basename(item.path)}…",
    )
    with open(item.path, "rb") as handle:
        data = {"description": item.alt_text} if item.alt_text else None
        resp = requests.post(
            f"{base}/api/v2/media",
            headers=headers,
            files={"file": (os.path.basename(item.path), handle, item.mime)},
            data=data,
            timeout=TIMEOUT,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Media upload failed ({resp.status_code}).")

    media = resp.json()
    media_id = media.get("id")
    if not media_id:
        raise RuntimeError("Media upload returned no id.")

    # 202 means the server is still processing; the id is unusable until 200.
    if resp.status_code == 202:
        for _ in range(MEDIA_POLL_ATTEMPTS):
            time.sleep(MEDIA_POLL_INTERVAL)
            check = requests.get(
                f"{base}/api/v1/media/{media_id}", headers=headers, timeout=TIMEOUT
            )
            if check.status_code == 200:
                break
        else:
            raise RuntimeError("Timed out waiting for the instance to process media.")
    return media_id


def _post_status(
    ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn, scheduled_at: Optional[str] = None
):
    """Upload media and create the status. Returns (response json, error)."""
    try:
        base, headers = _base(ctx), _headers(ctx)
        media_ids: List[str] = []
        for index, item in enumerate(req.media):
            media_ids.append(_upload_media(ctx, item, on_progress, index, len(req.media)))
    except ValueError as e:
        return None, str(e)
    except (OSError, RuntimeError) as e:
        return None, str(e)
    except requests.RequestException as e:
        return None, f"Could not reach the instance: {e}"

    body = req.body or ""
    if req.tags:
        hashtags = " ".join(f"#{t.lstrip('#')}" for t in req.tags)
        body = f"{body}\n\n{hashtags}".strip()
    if req.link_url:
        body = f"{body}\n{req.link_url}".strip()

    payload = {"status": body, "visibility": req.visibility or SPEC.capabilities.default_visibility}
    if media_ids:
        payload["media_ids[]"] = media_ids
    if scheduled_at:
        payload["scheduled_at"] = scheduled_at

    on_progress(80, "Scheduling…" if scheduled_at else "Posting…")
    try:
        resp = requests.post(
            f"{base}/api/v1/statuses", headers=headers, data=payload, timeout=TIMEOUT
        )
    except requests.RequestException as e:
        return None, f"Could not reach the instance: {e}"

    if resp.status_code >= 400:
        return None, f"Instance returned {resp.status_code}."
    return resp.json(), None


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    status, error = _post_status(ctx, req, on_progress)
    if error:
        return PublishResult(ok=False, message=error)
    return PublishResult(
        ok=True,
        remote_id=str(status.get("id") or ""),
        remote_url=status.get("url"),
        message="Posted to Mastodon.",
    )


def schedule(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    """Create a scheduled status; the server publishes it at ``req.publish_at``."""
    if req.publish_at is None:
        return PublishResult(ok=False, message="No publish time was given.")
    when = req.publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    scheduled, error = _post_status(ctx, req, on_progress, scheduled_at=when)
    if error:
        return PublishResult(ok=False, message=error)
    return PublishResult(
        ok=True,
        remote_id=str(scheduled.get("id") or ""),
        remote_url=None,
        message=f"Scheduled on Mastodon for {when}.",
    )


def scheduled_state(ctx: ConnCtx, remote_id: str) -> ScheduleCheck:
    """A scheduled status disappears from the server once it is published.

    It also disappears if someone deletes it on the server; the API cannot
    tell the two apart, which the message says.
    """
    try:
        resp = requests.get(
            f"{_base(ctx)}/api/v1/scheduled_statuses/{remote_id}",
            headers=_headers(ctx),
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"Could not check: {e}")
    except requests.RequestException as e:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"Could not reach the instance: {e}")
    if resp.status_code == 404:
        return ScheduleCheck(
            state=SCHEDULE_PUBLISHED,
            message=(
                "The server no longer holds it as scheduled, which means it went out at "
                "its time unless someone deleted it there. Mastodon does not link it to "
                "the post it became; delete that on the server if needed."
            ),
        )
    if resp.status_code >= 400:
        return ScheduleCheck(state=SCHEDULE_PENDING, message=f"Instance returned {resp.status_code}.")
    return ScheduleCheck(state=SCHEDULE_PENDING)


def delete(ctx: ConnCtx, req: DeleteRequest) -> DeleteResult:
    path = "scheduled_statuses" if req.pending_schedule else "statuses"
    try:
        resp = requests.delete(
            f"{_base(ctx)}/api/v1/{path}/{req.remote_id}",
            headers=_headers(ctx),
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return DeleteResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return DeleteResult(ok=False, message=f"Could not reach the instance: {e}")
    if resp.status_code == 404:
        return DeleteResult(ok=True, message="It was already gone from the server.")
    if resp.status_code >= 400:
        return DeleteResult(ok=False, message=f"Instance returned {resp.status_code}.")
    if req.pending_schedule:
        return DeleteResult(ok=True, message="Removed the scheduled post from Mastodon.")
    return DeleteResult(ok=True, message="Deleted from Mastodon.")
