"""Contracts shared by every connection provider.

Declaring capabilities as data rather than branching on provider names is what
keeps the Publish modal, the Social Accounts page and the publish path generic:
they read limits, rules and schedules straight from ``SPEC``.

The provider contract
=====================

A provider is one module under ``providers/``, listed in
``registry.PROVIDER_MODULES``. ``registry.contract_problems()`` checks the
pairing of declarations and functions below, and the registry tests run it for
every provider.

It declares, in ``SPEC = ProviderSpec(...)``:

- auth kinds, credential fields (secrets) and config fields (non-secret);
- ``capabilities`` (``Capabilities``): text and media limits, visibilities, and
  for the social family the account rules the publish path enforces:

  ``rate_limits``      our ceiling on posts per account per window, kept below
                       the platform's published limit; each entry names that
                       limit and its source. Enforced by ``rate_limits.check``
                       before every publish, failing closed.
  ``ai_disclosure``    when the platform's terms require a post to disclose AI
                       use, and how: a line in the body or a platform flag the
                       provider sets from ``PublishRequest.ai_disclosure``.
  ``bot_label``        how an account declares automation on that platform,
                       shown as guidance; ``readable`` providers report its
                       state from ``test()`` as ``config["bot_label_set"]``.
  ``native_scheduling`` the platform's own scheduling field and its limits.
                       Without it, Guaardvark's timer publishes at the time.
  ``deletable`` and ``delete_window_seconds``  whether Delete on platform
                       is offered, and for how long after posting.

It implements:

- ``test(ctx) -> (ok, message, discovered)``: probe the account; never posts.
- ``publish(ctx, request, on_progress) -> PublishResult``: post now. Social
  family only. Apply ``request.ai_disclosure`` when the disclosure mechanism is
  a platform flag (a text line is already in ``request.body``).
- ``schedule(ctx, request, on_progress) -> PublishResult`` and
  ``scheduled_state(ctx, remote_id) -> ScheduleCheck``: required together when
  ``native_scheduling`` is declared. ``schedule`` hands the post to the
  platform for ``request.publish_at``; ``scheduled_state`` says whether the
  platform has published it since.
- ``delete(ctx, request: DeleteRequest) -> DeleteResult``: required when
  ``deletable`` is declared. Removes the post (or a pending native schedule)
  from the platform.
- OAuth providers add ``authorize_url`` and ``exchange_code``.

Providers call only their platform's official API, never a browser session,
and raise nothing for an expected failure: they return a result with
``ok=False`` and a message a person can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# Connection families.
FAMILY_SOCIAL = "social"
FAMILY_AI_PROVIDER = "ai_provider"
FAMILY_MCP_SERVER = "mcp_server"
FAMILY_INTEGRATION = "integration"

# Authentication kinds.
AUTH_OAUTH2 = "oauth2"
AUTH_API_TOKEN = "api_token"
AUTH_APP_PASSWORD = "app_password"
AUTH_WEBHOOK_URL = "webhook_url"
AUTH_BROWSER_SESSION = "browser_session"

# Connection.status values.
STATUS_UNCONFIGURED = "unconfigured"
STATUS_CONNECTED = "connected"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"
STATUS_DISABLED = "disabled"


# When a platform's terms require a post to disclose AI use.
DISCLOSE_NEVER = "never"
# Required when the post carries media Guaardvark generated.
DISCLOSE_GENERATED_MEDIA = "generated_media"
# Required for generated media, and for text an agent (chat, MCP) drafted.
DISCLOSE_AI_ASSISTED = "ai_assisted"

# How a disclosure reaches the platform.
DISCLOSURE_TEXT = "text"  # a line appended to the post body
DISCLOSURE_FLAG = "platform_flag"  # a field of the platform's API, set by the provider

DEFAULT_DISCLOSURE_TEXT = "Made with AI."

# Requested-by sources whose text an agent wrote.
AGENT_SOURCES = ("chat", "mcp")


@dataclass(frozen=True)
class AIDisclosure:
    """A platform's AI disclosure rule and the mechanism that satisfies it.

    Where the rule applies the composer shows the switch forced on, with
    ``rule`` as the reason; elsewhere the person decides per post.
    """

    required_when: str = DISCLOSE_NEVER
    mechanism: str = DISCLOSURE_TEXT
    flag: str = ""
    text: str = DEFAULT_DISCLOSURE_TEXT
    rule: str = ""
    source: str = ""

    def required_for(self, generated_media: bool, requested_by: str) -> bool:
        if self.required_when == DISCLOSE_GENERATED_MEDIA:
            return generated_media
        if self.required_when == DISCLOSE_AI_ASSISTED:
            return generated_media or (requested_by or "") in AGENT_SOURCES
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_when": self.required_when,
            "mechanism": self.mechanism,
            "flag": self.flag,
            "text": self.text,
            "rule": self.rule,
            "source": self.source,
        }


# How an account tells others it is automated.
BOT_LABEL_NONE = "none"  # the platform defines no account label
BOT_LABEL_AUTOMATIC = "automatic"  # the platform labels it (bot accounts, webhooks)
BOT_LABEL_ACCOUNT_SETTING = "account_setting"  # the account holder sets it


@dataclass(frozen=True)
class BotLabel:
    """How an account declares automation on a platform, shown as guidance.

    ``readable`` means ``test()`` reads the label's state from the platform's
    API and records it as ``config["bot_label_set"]``. ``block_when_unset``
    refuses to publish from an account whose label reads as unset; set it only
    for a platform that requires the label of every account posting through its
    API and exposes it reliably.
    """

    kind: str = BOT_LABEL_NONE
    how: str = ""
    readable: bool = False
    api_field: str = ""
    block_when_unset: bool = False
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "how": self.how,
            "readable": self.readable,
            "api_field": self.api_field,
            "block_when_unset": self.block_when_unset,
            "source": self.source,
        }


@dataclass(frozen=True)
class RateLimit:
    """At most ``posts`` posts per account in any ``window_seconds``.

    Kept below ``platform_limit``, the platform's own published figure, which is
    stated with its ``source`` so the margin stays visible.
    """

    posts: int
    window_seconds: int
    platform_limit: str = ""
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "posts": self.posts,
            "window_seconds": self.window_seconds,
            "platform_limit": self.platform_limit,
            "source": self.source,
        }


@dataclass(frozen=True)
class NativeScheduling:
    """The platform's own scheduling: it holds the post and publishes it.

    ``min_lead_seconds`` is the platform's shortest accepted lead time;
    ``visibilities`` limits native scheduling to those visibilities (empty
    means any). Outside those limits Guaardvark's timer publishes instead.
    """

    field: str
    min_lead_seconds: int = 0
    visibilities: Tuple[str, ...] = ()
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "min_lead_seconds": self.min_lead_seconds,
            "visibilities": list(self.visibilities),
            "source": self.source,
        }


@dataclass(frozen=True)
class Capabilities:
    """What a platform accepts. Drives client-side validation and the UI."""

    text: bool = True
    max_text_chars: Optional[int] = None
    images: bool = False
    max_images: int = 0
    max_image_bytes: Optional[int] = None
    video: bool = False
    max_video_bytes: Optional[int] = None
    # Longest clip the platform accepts, in seconds; None = no stated limit.
    max_video_seconds: Optional[float] = None
    audio: bool = False
    requires_media: bool = False
    requires_public_url: bool = False
    supports_link: bool = True
    supports_title: bool = False
    supports_tags: bool = False
    visibilities: Tuple[str, ...] = ()
    default_visibility: str = "private"
    accepted_mime: Tuple[str, ...] = ()
    rate_limits: Tuple[RateLimit, ...] = ()
    ai_disclosure: AIDisclosure = AIDisclosure()
    bot_label: BotLabel = BotLabel()
    native_scheduling: Optional[NativeScheduling] = None
    deletable: bool = False
    # How long after posting the platform still allows a delete; None = no limit.
    delete_window_seconds: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "max_text_chars": self.max_text_chars,
            "images": self.images,
            "max_images": self.max_images,
            "max_image_bytes": self.max_image_bytes,
            "video": self.video,
            "max_video_bytes": self.max_video_bytes,
            "max_video_seconds": self.max_video_seconds,
            "audio": self.audio,
            "requires_media": self.requires_media,
            "requires_public_url": self.requires_public_url,
            "supports_link": self.supports_link,
            "supports_title": self.supports_title,
            "supports_tags": self.supports_tags,
            "visibilities": list(self.visibilities),
            "default_visibility": self.default_visibility,
            "accepted_mime": list(self.accepted_mime),
            "rate_limits": [r.to_dict() for r in self.rate_limits],
            "ai_disclosure": self.ai_disclosure.to_dict(),
            "bot_label": self.bot_label.to_dict(),
            "native_scheduling": (
                self.native_scheduling.to_dict() if self.native_scheduling else None
            ),
            "deletable": self.deletable,
            "delete_window_seconds": self.delete_window_seconds,
        }


@dataclass(frozen=True)
class CredentialField:
    """A secret input. Rendered as a password field with a masked hint."""

    name: str
    label: str
    required: bool = True
    help: str = ""
    placeholder: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "secret": True,
        }


@dataclass(frozen=True)
class ConfigField:
    """A non-secret input. Stored on ``Connection.config``."""

    name: str
    label: str
    required: bool = False
    default: str = ""
    help: str = ""
    choices: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "choices": list(self.choices),
            "secret": False,
        }


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the API and UI need to render and validate a provider."""

    provider: str
    family: str
    label: str
    auth_kinds: Tuple[str, ...]
    credential_fields: Tuple[CredentialField, ...] = ()
    config_fields: Tuple[ConfigField, ...] = ()
    capabilities: Capabilities = Capabilities()
    env_keys: Tuple[str, ...] = ()
    hint_field: Optional[str] = None
    docs_url: str = ""
    setup_help: str = ""
    review_required: bool = False
    beta: bool = False
    # True when the credential can post only into a channel its holder
    # manages (a Discord webhook). Only such a connection may be switched to
    # post without a per-post approval (gates.requires_approval).
    posts_to_owned_channel: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "family": self.family,
            "label": self.label,
            "auth_kinds": list(self.auth_kinds),
            "credential_fields": [f.to_dict() for f in self.credential_fields],
            "config_fields": [f.to_dict() for f in self.config_fields],
            "capabilities": self.capabilities.to_dict(),
            "env_keys": list(self.env_keys),
            "docs_url": self.docs_url,
            "setup_help": self.setup_help,
            "review_required": self.review_required,
            "beta": self.beta,
            "posts_to_owned_channel": self.posts_to_owned_channel,
        }


@dataclass
class MediaItem:
    """A resolved local asset ready to upload."""

    path: str
    mime: str
    bytes: int
    document_id: Optional[int] = None
    role: str = "primary"
    width: Optional[int] = None
    height: Optional[int] = None
    duration_s: Optional[float] = None
    alt_text: Optional[str] = None
    # From the Document's metadata: the model whose license asks to be named
    # (MiniMax H3) and whether the clip carries a generated soundtrack.
    attribution: Optional[str] = None
    has_audio: bool = False
    # Made by Guaardvark's generators, as its Document metadata records.
    generated: bool = False

    @property
    def kind(self) -> str:
        if self.mime.startswith("image/"):
            return "image"
        if self.mime.startswith("video/"):
            return "video"
        if self.mime.startswith("audio/"):
            return "audio"
        return "other"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "path": self.path,
            "mime": self.mime,
            "bytes": self.bytes,
            "role": self.role,
            "alt_text": self.alt_text,
            "generated": self.generated,
        }


@dataclass
class PublishRequest:
    """One post, already validated against the target's capabilities."""

    record_id: int
    connection_id: int
    body: str = ""
    title: Optional[str] = None
    link_url: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    visibility: str = "private"
    media: List[MediaItem] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    # Set the platform's AI flag when the disclosure mechanism is a flag.
    ai_disclosure: bool = False
    # For ``schedule()``: when the platform should publish it (naive local time).
    publish_at: Optional[datetime] = None


@dataclass
class PublishResult:
    ok: bool
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    message: str = ""


# ScheduleCheck.state values.
SCHEDULE_PENDING = "pending"  # the platform still holds it for later
SCHEDULE_PUBLISHED = "published"  # the platform has published it
SCHEDULE_GONE = "gone"  # the platform no longer has it, and did not publish it


@dataclass
class ScheduleCheck:
    """What ``scheduled_state()`` learned about a natively scheduled post."""

    state: str
    remote_url: Optional[str] = None
    message: str = ""


@dataclass
class DeleteRequest:
    """Remove one post from the platform.

    ``pending_schedule`` is true when the platform still holds the post for a
    future time, which some APIs delete through a different endpoint.
    """

    record_id: int
    remote_id: str
    remote_url: Optional[str] = None
    pending_schedule: bool = False


@dataclass
class DeleteResult:
    ok: bool
    message: str = ""


@dataclass
class ConnCtx:
    """What a provider receives: resolved secrets plus write-back callbacks.

    ``save_secrets`` exists so OAuth providers can persist a refreshed token
    without importing the store themselves.
    """

    connection: Dict[str, Any]
    config: Dict[str, Any]
    secrets: Dict[str, str]
    save_secrets: Callable[[Dict[str, str]], None] = lambda _values: None
    set_status: Callable[[str, Optional[str]], None] = lambda _status, _error: None


class ProviderError(Exception):
    """A provider call failed in a way worth showing the operator."""


ProgressFn = Callable[[int, str], None]
