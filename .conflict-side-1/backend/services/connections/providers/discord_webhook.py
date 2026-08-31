"""Discord publishing via an incoming webhook.

The webhook URL is itself the credential — anyone holding it can post to the
channel — so it is stored as a secret rather than in ``Connection.config``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Tuple

import requests

from backend.services.connections.base import (
    AUTH_WEBHOOK_URL,
    FAMILY_SOCIAL,
    Capabilities,
    ConnCtx,
    CredentialField,
    ProgressFn,
    ProviderSpec,
    PublishRequest,
    PublishResult,
)

logger = logging.getLogger(__name__)

TIMEOUT = 60
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

SPEC = ProviderSpec(
    provider="discord_webhook",
    family=FAMILY_SOCIAL,
    label="Discord (webhook)",
    auth_kinds=(AUTH_WEBHOOK_URL,),
    credential_fields=(
        CredentialField(
            name="webhook_url",
            label="Webhook URL",
            help="Channel → Edit Channel → Integrations → Webhooks → Copy Webhook URL.",
            placeholder="https://discord.com/api/webhooks/...",
        ),
    ),
    capabilities=Capabilities(
        max_text_chars=2000,
        images=True,
        max_images=10,
        max_image_bytes=MAX_ATTACHMENT_BYTES,
        video=True,
        max_video_bytes=MAX_ATTACHMENT_BYTES,
        audio=True,
        supports_title=False,
        supports_tags=False,
        visibilities=("public",),
        default_visibility="public",
    ),
    hint_field="webhook_url",
    docs_url="https://discord.com/developers/docs/resources/webhook",
    setup_help="Create a webhook on the target channel and paste its URL.",
)


def _webhook_url(ctx: ConnCtx) -> str:
    url = (ctx.secrets.get("webhook_url") or "").strip()
    if not url:
        raise ValueError("No webhook URL configured.")
    return url


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    """A GET on a webhook URL returns its metadata without posting anything."""
    try:
        resp = requests.get(_webhook_url(ctx), timeout=TIMEOUT)
    except ValueError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach Discord: {e}", {}

    if resp.status_code == 401:
        return False, "Webhook rejected — the URL may have been revoked.", {}
    if resp.status_code == 404:
        return False, "Webhook not found — check the URL.", {}
    if resp.status_code >= 400:
        return False, f"Discord returned {resp.status_code}.", {}

    data = resp.json()
    name = data.get("name") or "Discord webhook"
    channel = data.get("channel_id")
    return True, f"Connected as '{name}'.", {
        "handle": name,
        "display_name": name,
        "config": {"channel_id": channel} if channel else {},
    }


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    url = _webhook_url(ctx)
    content = req.body or ""
    if req.link_url:
        content = f"{content}\n{req.link_url}".strip()

    payload = {"content": content[: SPEC.capabilities.max_text_chars]}
    on_progress(30, "Sending to Discord…")

    files = {}
    handles = []
    try:
        for index, item in enumerate(req.media):
            handle = open(item.path, "rb")
            handles.append(handle)
            files[f"files[{index}]"] = (os.path.basename(item.path), handle, item.mime)

        if files:
            files["payload_json"] = (None, json.dumps(payload), "application/json")
            resp = requests.post(url, files=files, params={"wait": "true"}, timeout=TIMEOUT)
        else:
            resp = requests.post(url, json=payload, params={"wait": "true"}, timeout=TIMEOUT)
    except OSError as e:
        return PublishResult(ok=False, message=f"Could not read media: {e}")
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach Discord: {e}")
    finally:
        for handle in handles:
            handle.close()

    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "?")
        return PublishResult(ok=False, message=f"Rate limited by Discord; retry after {retry}s.")
    if resp.status_code >= 400:
        return PublishResult(ok=False, message=f"Discord returned {resp.status_code}.")

    on_progress(95, "Posted.")
    message = resp.json() if resp.content else {}
    message_id = message.get("id")
    channel_id = message.get("channel_id")
    remote_url = (
        f"https://discord.com/channels/@me/{channel_id}/{message_id}"
        if message_id and channel_id
        else None
    )
    return PublishResult(
        ok=True,
        remote_id=message_id,
        remote_url=remote_url,
        message="Posted to Discord.",
    )
