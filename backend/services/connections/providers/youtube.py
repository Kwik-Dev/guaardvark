"""YouTube publishing via the Data API v3.

Uploads are forced to ``private``: making a video public is a deliberate step
the operator takes in YouTube Studio, not a side effect of generating one.

The resumable upload protocol is driven directly over HTTPS rather than through
google-api-python-client, which keeps the backend venv free of that dependency
and gives real per-chunk progress for the multi-gigabyte case.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Tuple

import requests

from backend.services.connections.base import (
    AUTH_OAUTH2,
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

TIMEOUT = 120
CHUNK_BYTES = 8 * 1024 * 1024
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3"
SCOPES = ["https://www.googleapis.com/auth/youtube"]
CATEGORY_SCIENCE_TECH = "28"

SPEC = ProviderSpec(
    provider="youtube",
    family=FAMILY_SOCIAL,
    label="YouTube",
    auth_kinds=(AUTH_OAUTH2,),
    credential_fields=(
        CredentialField(
            name="client_id",
            label="OAuth client ID",
            help="From an OAuth 'Desktop app' client in Google Cloud Console.",
        ),
        CredentialField(name="client_secret", label="OAuth client secret"),
        CredentialField(
            name="refresh_token",
            label="Refresh token",
            required=False,
            help="Filled in automatically once you complete the authorization step.",
        ),
    ),
    capabilities=Capabilities(
        text=True,
        max_text_chars=5000,
        images=False,
        video=True,
        max_video_bytes=128 * 1024 * 1024 * 1024,
        requires_media=True,
        supports_title=True,
        supports_tags=True,
        visibilities=("private", "unlisted", "public"),
        default_visibility="private",
    ),
    hint_field="client_secret",
    docs_url="https://developers.google.com/youtube/v3/guides/uploading_a_video",
    setup_help=(
        "Create an OAuth Desktop-app client in Google Cloud Console, enable the "
        "YouTube Data API v3, then authorize this connection."
    ),
)


def authorize_url(ctx: ConnCtx, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    client_id = (ctx.secrets.get("client_id") or "").strip()
    if not client_id:
        raise ValueError("No OAuth client ID configured.")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # Without this, Google omits the refresh token on re-authorization.
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_code(ctx: ConnCtx, code: str, redirect_uri: str) -> Dict[str, str]:
    resp = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}).")
    payload = resp.json()
    refresh = payload.get("refresh_token")
    if not refresh:
        raise RuntimeError(
            "Google returned no refresh token. Revoke the app's access and retry."
        )
    return {"refresh_token": refresh, "access_token": payload.get("access_token", "")}


def _access_token(ctx: ConnCtx) -> str:
    """Exchange the stored refresh token for a fresh access token."""
    refresh = (ctx.secrets.get("refresh_token") or "").strip()
    if not refresh:
        raise ValueError("Not authorized yet — complete the Google sign-in step.")

    resp = requests.post(
        TOKEN_URI,
        data={
            "refresh_token": refresh,
            "client_id": (ctx.secrets.get("client_id") or "").strip(),
            "client_secret": (ctx.secrets.get("client_secret") or "").strip(),
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code == 400:
        raise RuntimeError("Refresh token rejected — reauthorize this connection.")
    if resp.status_code >= 400:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}).")

    token = (resp.json() or {}).get("access_token")
    if not token:
        raise RuntimeError("Token refresh returned no access token.")
    return token


def test(ctx: ConnCtx) -> Tuple[bool, str, Dict]:
    try:
        token = _access_token(ctx)
        resp = requests.get(
            f"{API_URL}/channels",
            headers={"Authorization": f"Bearer {token}"},
            params={"part": "snippet", "mine": "true"},
            timeout=TIMEOUT,
        )
    except ValueError as e:
        return False, str(e), {}
    except RuntimeError as e:
        return False, str(e), {}
    except requests.RequestException as e:
        return False, f"Could not reach YouTube: {e}", {}

    if resp.status_code >= 400:
        return False, f"YouTube returned {resp.status_code}.", {}

    items = (resp.json() or {}).get("items") or []
    if not items:
        return False, "No channel is associated with this Google account.", {}

    snippet = items[0].get("snippet") or {}
    title = snippet.get("title") or "YouTube channel"
    return True, f"Connected to '{title}'.", {
        "handle": snippet.get("customUrl") or title,
        "display_name": title,
        "config": {"channel_id": items[0].get("id")},
    }


def _start_resumable(token: str, body: Dict, item) -> str:
    resp = requests.post(
        UPLOAD_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(item.bytes),
            "X-Upload-Content-Type": item.mime,
        },
        params={"uploadType": "resumable", "part": "snippet,status"},
        data=json.dumps(body),
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Could not start the upload ({resp.status_code}).")
    location = resp.headers.get("Location")
    if not location:
        raise RuntimeError("Upload session returned no Location header.")
    return location


def _upload_chunks(session_url: str, item, on_progress: ProgressFn) -> Dict:
    total = item.bytes
    sent = 0
    with open(item.path, "rb") as handle:
        while sent < total:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            resp = requests.put(
                session_url,
                headers={
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {sent}-{end}/{total}",
                },
                data=chunk,
                timeout=TIMEOUT,
            )
            # 308 means the server wants the next chunk.
            if resp.status_code in (200, 201):
                on_progress(95, "Finalising…")
                return resp.json()
            if resp.status_code != 308:
                raise RuntimeError(f"Upload failed ({resp.status_code}).")
            sent = end + 1
            on_progress(20 + int(70 * sent / max(total, 1)), f"Uploading… {sent * 100 // total}%")
    raise RuntimeError("Upload ended without a response from YouTube.")


def publish(ctx: ConnCtx, req: PublishRequest, on_progress: ProgressFn) -> PublishResult:
    videos = [m for m in req.media if m.kind == "video"]
    if not videos:
        return PublishResult(ok=False, message="YouTube requires a video file.")
    item = videos[0]

    # Public requires both an explicit request and an operator opt-in.
    visibility = req.visibility or "private"
    if visibility != "private" and not _public_publish_allowed():
        visibility = "private"

    body = {
        "snippet": {
            "title": (req.title or os.path.basename(item.path))[:100],
            "description": (req.body or "")[:5000],
            "tags": list(req.tags or []),
            "categoryId": CATEGORY_SCIENCE_TECH,
        },
        "status": {"privacyStatus": visibility, "selfDeclaredMadeForKids": False},
    }

    try:
        on_progress(10, "Authenticating…")
        token = _access_token(ctx)
        on_progress(15, "Starting upload…")
        session_url = _start_resumable(token, body, item)
        result = _upload_chunks(session_url, item, on_progress)
    except ValueError as e:
        return PublishResult(ok=False, message=str(e))
    except (OSError, RuntimeError) as e:
        return PublishResult(ok=False, message=str(e))
    except requests.RequestException as e:
        return PublishResult(ok=False, message=f"Could not reach YouTube: {e}")

    video_id = result.get("id")
    return PublishResult(
        ok=True,
        remote_id=video_id,
        remote_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        message=f"Uploaded to YouTube ({visibility}).",
    )


def _public_publish_allowed() -> bool:
    try:
        from backend.utils.settings_utils import get_setting

        return (get_setting("youtube_allow_public_publish", "false") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    except Exception:  # noqa: BLE001 - default to the safe answer
        return False
