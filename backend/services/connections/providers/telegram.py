"""Telegram publishing via the Bot API.

The bot must already be a member of the target chat (or an admin of the target
channel); Telegram offers no API to add it, so ``test()`` reports reachability
of the bot rather than of the chat.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Tuple

import requests

from backend.services.connections.base import (
    AUTH_API_TOKEN,
    BOT_LABEL_AUTOMATIC,
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
CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096

SPEC = ProviderSpec(
    provider="telegram",
    family=FAMILY_SOCIAL,
    label="Telegram",
    auth_kinds=(AUTH_API_TOKEN,),
    credential_fields=(
        CredentialField(
            name="bot_token",
            label="Bot token",
            help="Create a bot with @BotFather and paste the token it gives you.",
        ),
    ),
    config_fields=(
        ConfigField(
            name="chat_id",
            label="Chat or channel ID",
            required=True,
            help="Numeric id, or @channelname. The bot must already be a member.",
        ),
    ),
    capabilities=Capabilities(
        # A media post uses the caption field, which is shorter than a message.
        max_text_chars=CAPTION_LIMIT,
        images=True,
        max_images=1,
        max_image_bytes=10 * 1024 * 1024,
        video=True,
        max_video_bytes=50 * 1024 * 1024,
        audio=True,
        visibilities=("public",),
        default_visibility="public",
        # A connection posts to one chat.
        rate_limits=(
            RateLimit(
                posts=10,
                window_seconds=3600,
                platform_limit="about one message per second in a chat; 20 per minute in a group",
                source="https://core.telegram.org/bots/faq",
            ),
        ),
        bot_label=BotLabel(
            kind=BOT_LABEL_AUTOMATIC,
            how="Telegram shows every bot account as a bot; there is nothing to set.",
            source="https://core.telegram.org/bots",
        ),
        deletable=True,
        # deleteMessage: "A message can only be deleted if it was sent less than
        # 48 hours ago." https://core.telegram.org/bots/api#deletemessage
        delete_window_seconds=48 * 3600,
    ),
    hint_field="bot_token",
    docs_url="https://core.telegram.org/bots/api",
    setup_help="Create a bot with @BotFather, then add it to the target chat.",
)


def _api(ctx: ConnCtx, method: str) -> str:
    token = (ctx.secrets.get("bot_token") or "").strip()
    if not token:
        raise ValueError("No bot token configured.")
    return f"https://api.telegram.org/bot{token}/{method}"


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        resp = requests.get(_api(ctx, "getMe"), timeout=TIMEOUT)
    except ValueError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach Telegram: {e}", {}

    if resp.status_code == 401:
        return False, "Token rejected by Telegram.", {}
    if resp.status_code >= 400:
        return False, f"Telegram returned {resp.status_code}.", {}

    bot = (resp.json() or {}).get("result") or {}
    username = bot.get("username") or "bot"
    if not (ctx.config.get("chat_id") or "").strip():
        return True, f"Bot @{username} reachable — set a chat ID to publish.", {
            "handle": f"@{username}",
            "display_name": bot.get("first_name") or username,
        }
    return True, f"Connected as @{username}.", {
        "handle": f"@{username}",
        "display_name": bot.get("first_name") or username,
    }


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    chat_id = (ctx.config.get("chat_id") or "").strip()
    if not chat_id:
        return PublishResult(ok=False, message="No chat ID configured.")

    text = req.body or ""
    if req.link_url:
        text = f"{text}\n{req.link_url}".strip()

    primary = req.media[0] if req.media else None
    on_progress(40, "Sending to Telegram…")

    try:
        if primary is None:
            resp = requests.post(
                _api(ctx, "sendMessage"),
                data={"chat_id": chat_id, "text": text[:MESSAGE_LIMIT]},
                timeout=TIMEOUT,
            )
        else:
            method, field = {
                "image": ("sendPhoto", "photo"),
                "video": ("sendVideo", "video"),
                "audio": ("sendAudio", "audio"),
            }.get(primary.kind, ("sendDocument", "document"))
            with open(primary.path, "rb") as handle:
                resp = requests.post(
                    _api(ctx, method),
                    data={"chat_id": chat_id, "caption": text[:CAPTION_LIMIT]},
                    files={field: (os.path.basename(primary.path), handle, primary.mime)},
                    timeout=TIMEOUT,
                )
    except ValueError as e:
        return PublishResult(ok=False, message=str(e))
    except OSError as e:
        return PublishResult(ok=False, message=f"Could not read media: {e}")
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach Telegram: {e}")

    if resp.status_code >= 400:
        detail = ""
        try:
            detail = (resp.json() or {}).get("description") or ""
        except ValueError:
            pass
        return PublishResult(
            ok=False,
            message=f"Telegram returned {resp.status_code}{f': {detail}' if detail else ''}.",
        )

    result = (resp.json() or {}).get("result") or {}
    message_id = result.get("message_id")
    chat = result.get("chat") or {}
    username = chat.get("username")
    remote_url = (
        f"https://t.me/{username}/{message_id}" if username and message_id else None
    )
    on_progress(95, "Sent.")
    return PublishResult(
        ok=True,
        remote_id=str(message_id) if message_id else None,
        remote_url=remote_url,
        message="Sent to Telegram.",
    )


def delete(ctx: ConnCtx, req: DeleteRequest) -> DeleteResult:
    chat_id = (ctx.config.get("chat_id") or "").strip()
    if not chat_id:
        return DeleteResult(ok=False, message="No chat ID configured.")
    try:
        resp = requests.post(
            _api(ctx, "deleteMessage"),
            data={"chat_id": chat_id, "message_id": req.remote_id},
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return DeleteResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return DeleteResult(ok=False, message=f"Could not reach Telegram: {e}")

    detail = ""
    try:
        detail = (resp.json() or {}).get("description") or ""
    except ValueError:
        pass
    if resp.status_code >= 400:
        if "not found" in detail.lower():
            return DeleteResult(ok=True, message="The message was already gone from the chat.")
        return DeleteResult(
            ok=False,
            message=f"Telegram returned {resp.status_code}{f': {detail}' if detail else ''}.",
        )
    return DeleteResult(ok=True, message="Deleted from Telegram.")
