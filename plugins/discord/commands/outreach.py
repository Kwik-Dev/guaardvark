"""
Outreach cog — quietly watches configured channels for messages we can usefully
respond to (Ollama / ComfyUI / local-AI / RAG / self-hosted topics) and drafts a
reply via the backend's /api/social-outreach/draft-comment endpoint. The backend
queues the draft for review, or marks it approved when it may post on its own.

Replies are sent only by poll_approved_drafts, for approved drafts, after the
cadence check, the claim and the /submit step (where a reject still stops it).
Drafting never sends.

This is the lowest-risk path of the three loops: API-only, no servo, no browser.
If something goes wrong here it's confined to a Discord channel — no shadow-ban
risk on a karma-tracked platform.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

from core.api_client import GuaardvarkClient, APIError

logger = logging.getLogger("discord_bot")

# We track which message ids we've already considered so the loop doesn't keep
# re-drafting replies for the same prompt every 10 minutes.
_REPO_ROOT = Path(os.environ.get("GUAARDVARK_ROOT") or Path(__file__).resolve().parents[3])
_SEEN_FILE = _REPO_ROOT / "data" / "social_outreach" / "discord_seen.json"

# Default poll interval if config doesn't set one. 10 minutes mirrors the plan.
DEFAULT_POLL_SECONDS = 600

# How far back to look on first run / when no last_seen is saved.
INITIAL_LOOKBACK_SECONDS = 30 * 60

# A reply that went out but that /record-post did not take counts against
# cadence here for the backend's daily window; after that it is not retried.
UNRECORDED_WINDOW_SECONDS = 24 * 3600


# Local-side relevance regex — matches the persona.RELEVANCE_KEYWORDS list on
# the backend, kept here so we don't waste a backend call on obvious misses.
_RELEVANCE_RX = re.compile(
    r"\b(ollama|local\s*llm|llama\.cpp|self\s*host(ed)?|local\s*ai|"
    r"comfy\s*ui|comfyui|stable\s*diffusion|video\s*gen|text2video|"
    r"upscal(e|ing)|esrgan|"
    r"\brag\b|retrieval|llamaindex|"
    r"swarm|multi[\s-]?agent|"
    r"voice|whisper|tts)\b",
    re.IGNORECASE,
)


def _looks_relevant(text: str) -> bool:
    if not text or len(text) < 20:
        return False
    return bool(_RELEVANCE_RX.search(text))


class OutreachCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api_client: GuaardvarkClient, config: dict):
        self.bot = bot
        self.api = api_client
        self.config = config
        outreach_cfg = (config.get("outreach") or {})
        self.enabled_in_config = bool(outreach_cfg.get("enabled", False))
        self.channels: list[int] = [int(c) for c in outreach_cfg.get("channels", []) if c]
        self.poll_seconds = int(outreach_cfg.get("poll_interval_seconds", DEFAULT_POLL_SECONDS))
        self.max_per_pass = int(outreach_cfg.get("max_replies_per_pass", 1))

        # OrderedDict gives us O(1) membership AND deterministic FIFO eviction —
        # naked sets are unordered, so "keep last 500" used to drop random IDs
        # and re-draft already-seen messages on the next pass.
        self._seen: dict[int, "OrderedDict[int, None]"] = {}
        self._last_seen_ts: dict[int, float] = {}
        self._load_seen()

        # Replies that went out but that /record-post did not take (a 403 while
        # outreach is switched off, a network error). The backend's cadence
        # does not know them, so the cadence check counts them, and each poll
        # offers them to /record-post again. Oldest first; in memory only.
        self._unrecorded: list[dict] = []

        # Hook the loop interval AFTER reading config so poll_seconds takes effect.
        self.poll_loop.change_interval(seconds=self.poll_seconds)
        self.poll_approved_drafts.start()
        self.poll_loop.start()

    def cog_unload(self):
        self.poll_loop.cancel()
        self.poll_approved_drafts.cancel()
        self._save_seen()

    # --- seen-state persistence -----------------------------------------

    def _load_seen(self):
        if not _SEEN_FILE.exists():
            return
        try:
            data = json.loads(_SEEN_FILE.read_text())
            self._seen = {int(k): OrderedDict.fromkeys(int(x) for x in v) for k, v in data.get("seen", {}).items()}
            self._last_seen_ts = {int(k): float(v) for k, v in data.get("last_ts", {}).items()}
        except Exception as e:
            logger.warning("outreach: failed to load seen state: %s", e)

    def _save_seen(self):
        try:
            _SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "seen": {str(k): list(v) for k, v in self._seen.items()},
                "last_ts": {str(k): v for k, v in self._last_seen_ts.items()},
            }
            _SEEN_FILE.write_text(json.dumps(payload))
        except Exception as e:
            logger.warning("outreach: failed to save seen state: %s", e)

    def _mark_seen(self, channel_id: int, msg_id: int):
        seen = self._seen.setdefault(channel_id, OrderedDict())
        seen[msg_id] = None
        # Bound memory — keep last 500 per channel (FIFO eviction).
        while len(seen) > 500:
            seen.popitem(last=False)

    @tasks.loop(seconds=60)
    async def poll_approved_drafts(self):
        if not self.enabled_in_config:
            return
            
        try:
            status = await self.api._get("/social-outreach/status")
            if not status.get("enabled"):
                return
        except Exception:
            return

        await self._retry_unrecorded()

        try:
            approved = await self.api._get("/social-outreach/approved")
        except Exception as e:
            logger.warning("outreach: failed to fetch approved drafts: %s", e)
            return

        posted_this_tick = False
        for row in approved:
            if posted_this_tick:
                break
            if row.get("platform") != "discord":
                continue

            audit_id = row.get("id")
            target_url = row.get("target_url")
            if not target_url or not audit_id:
                continue

            # Cadence before claim — leave row approved if blocked.
            try:
                status = await self.api._get("/social-outreach/status")
                cadence = (status.get("cadence") or {}).get("discord") or {}
                if cadence.get("redis") == "unavailable":
                    logger.info("outreach: discord cadence redis unavailable, skipping post")
                    break
                posts = int(cadence.get("posts_in_24h") or 0) + len(self._unrecorded)
                cap = int(cadence.get("daily_cap") or 8)
                last_ago = self._last_post_seconds_ago(cadence.get("last_post_seconds_ago"))
                min_gap = int(cadence.get("min_gap_s") or 1800)
                if posts >= cap:
                    logger.info("outreach: discord daily cap hit (%s/%s)", posts, cap)
                    break
                if last_ago is not None and int(last_ago) < min_gap:
                    logger.info("outreach: discord gap not elapsed (%ss < %ss)", last_ago, min_gap)
                    break
            except Exception as e:
                logger.warning("outreach: cadence check failed, skipping: %s", e)
                break

            # Claim before send so overlapping 60s polls cannot double-reply.
            try:
                await self.api._post(f"/social-outreach/claim/{audit_id}")
            except Exception as e:
                logger.info("outreach: claim failed for draft %s (race?): %s", audit_id, e)
                continue

            # target_url format: https://discord.com/channels/{guild}/{channel}/{msg.id}
            parts = target_url.split("/")
            if len(parts) < 3:
                await self.api._post(f"/social-outreach/reject/{audit_id}")
                continue

            try:
                channel_id = int(parts[-2])
                msg_id = int(parts[-1])
            except ValueError:
                await self.api._post(f"/social-outreach/reject/{audit_id}")
                continue

            channel = self.bot.get_channel(channel_id)
            if not channel:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception:
                    await self.api._post(f"/social-outreach/reject/{audit_id}")
                    continue

            try:
                msg = await channel.fetch_message(msg_id)
                draft = row.get("draft_text", "")
                if draft:
                    # The last moment a reject can stop this draft: the backend
                    # moves it processing -> submitting, and refuses if it was
                    # rejected after the claim. Anything but a yes means no send.
                    try:
                        await self.api._post(f"/social-outreach/submit/{audit_id}")
                    except Exception as e:
                        logger.info("outreach: draft %s not sent, submit refused: %s", audit_id, e)
                        continue
                    await msg.reply(draft, mention_author=False)
                    posted_this_tick = True

                    await self._record_post({
                        "audit_id": audit_id,
                        "platform": "discord",
                        "posted_text": draft,
                        "target_url": target_url,
                        "target_thread_id": str(msg_id),
                    })
            except discord.NotFound:
                logger.warning("outreach: msg %s not found, rejecting draft %s", msg_id, audit_id)
                await self._give_up(audit_id)
            except Exception as e:
                logger.warning("outreach: failed to post approved draft %s: %s", audit_id, e)
                await self._give_up(audit_id)

    async def _record_post(self, payload: dict) -> None:
        """Tell the backend a reply went out.

        When /record-post does not take it, the reply still counts: it is kept
        in ``_unrecorded`` for the cadence check and retried on each poll.
        """
        try:
            await self.api._post("/social-outreach/record-post", json=payload)
        except Exception as e:
            logger.error(
                "outreach: draft %s WAS SENT but record-post failed (%s); counting it against "
                "discord cadence here and retrying each poll",
                payload.get("audit_id"), e,
            )
            self._unrecorded.append({"payload": payload, "sent_at": time.time()})

    async def _retry_unrecorded(self) -> None:
        now = time.time()
        pending = []
        for item in self._unrecorded:
            audit_id = item["payload"].get("audit_id")
            if now - item["sent_at"] > UNRECORDED_WINDOW_SECONDS:
                logger.error("outreach: draft %s was sent but never recorded; no longer retried", audit_id)
                continue
            try:
                await self.api._post("/social-outreach/record-post", json=item["payload"])
                logger.info("outreach: draft %s recorded on retry", audit_id)
            except Exception as e:
                logger.warning("outreach: draft %s still not recorded: %s", audit_id, e)
                pending.append(item)
        self._unrecorded = pending

    def _last_post_seconds_ago(self, backend_ago) -> Optional[int]:
        """Seconds since the last Discord reply: the backend's figure, or the
        newest unrecorded reply when that is more recent."""
        if not self._unrecorded:
            return backend_ago
        local_ago = int(time.time() - self._unrecorded[-1]["sent_at"])
        return local_ago if backend_ago is None else min(int(backend_ago), local_ago)

    async def _give_up(self, audit_id) -> None:
        """Reject a claimed draft that could not be sent.

        Once the send has begun the backend refuses the reject, because the
        reply may be out; the row is then closed by the backend's stuck-row
        reaper. A refusal raised from here would stop the polling loop, so it
        is logged instead.
        """
        try:
            await self.api._post(f"/social-outreach/reject/{audit_id}")
        except Exception as e:
            logger.warning("outreach: draft %s not rejected, left for the reaper: %s", audit_id, e)

    @poll_approved_drafts.before_loop
    async def _before_approved_loop(self):
        await self.bot.wait_until_ready()

    # --- the loop --------------------------------------------------------

    @tasks.loop(seconds=DEFAULT_POLL_SECONDS)
    async def poll_loop(self):
        if not self.enabled_in_config:
            return
        if not self.channels:
            return

        # Cheap pre-flight — skip the whole pass if backend says outreach is killed.
        # (We still draft+queue when supervised, but if killed we don't even draft.)
        try:
            status = await self.api._get("/social-outreach/status")
        except Exception as e:
            logger.warning("outreach: status check failed, skipping pass: %s", e)
            return

        if not status.get("enabled"):
            logger.debug("outreach: kill switch is off, skipping pass")
            return

        for channel_id in self.channels:
            try:
                await self._process_channel(channel_id, supervised=bool(status.get("supervised")))
            except Exception:
                logger.exception("outreach: error processing channel %s", channel_id)
            finally:
                self._save_seen()

    @poll_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()
        logger.info("outreach: poll loop ready, %d channels, every %ds", len(self.channels), self.poll_seconds)

    async def _process_channel(self, channel_id: int, supervised: bool):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                logger.warning("outreach: cannot fetch channel %s: %s", channel_id, e)
                return

        seen = self._seen.setdefault(channel_id, OrderedDict())
        last_ts = self._last_seen_ts.get(channel_id)
        kwargs = {"limit": 50}
        if last_ts is not None:
            from datetime import datetime, timezone
            kwargs["after"] = datetime.fromtimestamp(last_ts, tz=timezone.utc)
        # else: first run, just take the most recent 50 — _looks_relevant will filter

        replies_this_pass = 0
        try:
            messages = [m async for m in channel.history(**kwargs)]
        except discord.Forbidden:
            logger.warning("outreach: no read perms on channel %s", channel_id)
            return
        except Exception as e:
            logger.warning("outreach: history fetch failed on %s: %s", channel_id, e)
            return

        # Process oldest-first so we reply to the earliest match first.
        messages.sort(key=lambda m: m.created_at)

        newest_ts = last_ts or 0.0
        for msg in messages:
            ts = msg.created_at.timestamp()
            newest_ts = max(newest_ts, ts)

            if msg.id in seen:
                continue
            self._mark_seen(channel_id, msg.id)

            # Skip our own messages and other bots — never reply to bots.
            if msg.author.bot or (self.bot.user and msg.author.id == self.bot.user.id):
                continue

            if not _looks_relevant(msg.content):
                continue

            if replies_this_pass >= self.max_per_pass:
                continue

            # Found a candidate. Draft via backend.
            await self._handle_candidate(msg, supervised=supervised)
            replies_this_pass += 1

        self._last_seen_ts[channel_id] = newest_ts

    async def _handle_candidate(self, msg: discord.Message, supervised: bool):
        thread_context = msg.content
        # Pull a couple recent messages for context (ignore bots).
        try:
            ctx_msgs = []
            async for prev in msg.channel.history(limit=5, before=msg):
                if prev.author.bot:
                    continue
                ctx_msgs.append(f"{prev.author.display_name}: {prev.content}")
            if ctx_msgs:
                thread_context = "\n".join(reversed(ctx_msgs)) + f"\n\n>>> {msg.author.display_name}: {msg.content}"
        except Exception:
            pass

        target_url = f"https://discord.com/channels/{msg.guild.id if msg.guild else '@me'}/{msg.channel.id}/{msg.id}"

        try:
            result = await self.api._post(
                "/social-outreach/draft-comment",
                json={
                    "platform": "discord",
                    "thread_context": thread_context,
                    "target_url": target_url,
                    "target_thread_id": str(msg.id),
                    "mode": "comment",
                },
            )
        except APIError as e:
            logger.warning("outreach: draft-comment failed: %s", e)
            return
        except Exception as e:
            logger.warning("outreach: draft-comment error: %s", e)
            return

        draft = (result.get("draft") or "").strip()
        grade = float(result.get("grade") or 0.0)
        would_post = bool(result.get("would_post"))
        audit_id = result.get("audit_id")

        logger.info(
            "outreach: drafted (channel=%s, msg=%s, grade=%.2f, would_post=%s, supervised=%s, len=%d)",
            msg.channel.id, msg.id, grade, would_post, supervised, len(draft),
        )

        # A would_post draft is stored as approved; poll_approved_drafts sends it.
        if would_post and draft and not supervised:
            logger.info("outreach: draft %s approved, poll_approved_drafts will send it", audit_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(OutreachCog(bot, bot.api_client, bot.config))
