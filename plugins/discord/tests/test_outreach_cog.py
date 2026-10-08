"""The outreach cog sends a reply only from poll_approved_drafts.

_handle_candidate drafts through /draft-comment and stops there, even when the
backend says the draft may post on its own. The approved draft goes out on the
next poll, after the cadence check, the claim and /submit. Discord and the
backend are stand-ins: FakeBackend keeps one status per draft and answers the
routes the cog calls.
"""
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from discord.ext import tasks

from commands import outreach
from commands.outreach import OutreachCog
from core.api_client import APIError

DRAFT = "A 12GB card runs the 8B models fine."
CHANNEL_ID = 555
MSG_ID = 999


class FakeBackend:
    """The /social-outreach routes the cog uses, with the backend's transitions
    and its Discord cadence (a recorded post starts the 30-minute gap)."""

    def __init__(self, *, would_post=True):
        self.would_post = would_post
        self.rows = {}
        self.calls = []
        self.recorded_at = []
        self.record_fails = False

    async def _get(self, path):
        if path == "/social-outreach/status":
            last = int(time.time() - self.recorded_at[-1]) if self.recorded_at else None
            return {"enabled": True, "supervised": False, "cadence": {"discord": {
                "posts_in_24h": len(self.recorded_at), "daily_cap": 8,
                "last_post_seconds_ago": last, "min_gap_s": 1800,
            }}}
        if path == "/social-outreach/approved":
            return [dict(row, id=i) for i, row in self.rows.items() if row["status"] == "approved"]
        raise AssertionError(f"unexpected GET {path}")

    async def _post(self, path, json=None):
        self.calls.append(path)
        if path == "/social-outreach/draft-comment":
            audit_id = len(self.rows) + 1
            self.rows[audit_id] = {
                "platform": "discord", "draft_text": DRAFT, "target_url": json["target_url"],
                "status": "approved" if self.would_post else "drafted",
            }
            return {"draft": DRAFT, "grade": 0.9, "would_post": self.would_post, "audit_id": audit_id}
        if path == "/social-outreach/record-post":
            if self.record_fails:
                raise APIError("outreach disabled (kill switch)", status_code=403)
            self.rows[json["audit_id"]]["status"] = "posted"
            self.recorded_at.append(time.time())
            return {"ok": True}
        verb, audit_id = path.rsplit("/", 2)[-2:]
        row = self.rows[int(audit_id)]
        moves = {"claim": ("approved", "processing"), "submit": ("processing", "submitting")}
        if verb not in moves or row["status"] != moves[verb][0]:
            raise APIError(f"cannot {verb} from {row['status']}", status_code=409)
        row["status"] = moves[verb][1]
        return dict(row, id=int(audit_id))


def _aiter(items):
    async def gen():
        for item in items:
            yield item
    return gen()


def _message():
    msg = MagicMock()
    msg.id = MSG_ID
    msg.content = "Which GPU do I need to run ollama with a local llm?"
    msg.author.bot = False
    msg.author.display_name = "someone"
    msg.guild.id = 1
    msg.channel.id = CHANNEL_ID
    msg.channel.history = MagicMock(side_effect=lambda **kwargs: _aiter([]))
    msg.reply = AsyncMock()
    return msg


@pytest.fixture
def cog(tmp_path, sample_config):
    config = {**sample_config, "outreach": {"enabled": True, "channels": [CHANNEL_ID]}}
    bot = MagicMock()
    with patch.object(outreach, "_SEEN_FILE", tmp_path / "discord_seen.json"), \
            patch.object(tasks.Loop, "start"):
        yield OutreachCog(bot, FakeBackend(), config)


def _route_to(cog, msg):
    """Make the poller find ``msg`` when it resolves a draft's target_url."""
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=msg)
    cog.bot.get_channel = MagicMock(return_value=channel)
    return channel


@pytest.mark.asyncio
async def test_a_would_post_draft_is_not_sent_by_the_candidate_pass(cog):
    msg = _message()

    await cog._handle_candidate(msg, supervised=False)

    msg.reply.assert_not_called()
    assert cog.api.calls == ["/social-outreach/draft-comment"]
    assert cog.api.rows[1]["status"] == "approved"


@pytest.mark.asyncio
async def test_the_poller_claims_submits_replies_and_records_once(cog):
    msg = _message()
    cog.api.rows[7] = {"platform": "discord", "draft_text": DRAFT, "status": "approved",
                       "target_url": f"https://discord.com/channels/1/{CHANNEL_ID}/{MSG_ID}"}
    channel = _route_to(cog, msg)

    await cog.poll_approved_drafts()

    cog.bot.get_channel.assert_called_once_with(CHANNEL_ID)
    channel.fetch_message.assert_awaited_once_with(MSG_ID)
    msg.reply.assert_awaited_once_with(DRAFT, mention_author=False)
    assert cog.api.calls == [
        "/social-outreach/claim/7",
        "/social-outreach/submit/7",
        "/social-outreach/record-post",
    ]
    assert cog.api.rows[7]["status"] == "posted"


@pytest.mark.asyncio
async def test_one_candidate_gives_one_reply_from_the_poller(cog):
    msg = _message()
    _route_to(cog, msg)

    await cog._handle_candidate(msg, supervised=False)
    msg.reply.assert_not_called()

    await cog.poll_approved_drafts()
    await cog.poll_approved_drafts()

    msg.reply.assert_awaited_once_with(DRAFT, mention_author=False)
    assert cog.api.calls.count("/social-outreach/record-post") == 1


@pytest.mark.asyncio
async def test_a_draft_rejected_after_the_claim_is_not_sent(cog):
    msg = _message()
    _route_to(cog, msg)
    await cog._handle_candidate(msg, supervised=False)

    async def rejected_at_submit(path, json=None):
        if path.startswith("/social-outreach/submit/"):
            raise APIError("cannot submit from status 'rejected'", status_code=409)
        return await FakeBackend._post(cog.api, path, json=json)

    cog.api._post = rejected_at_submit
    await cog.poll_approved_drafts()

    msg.reply.assert_not_called()


@pytest.mark.asyncio
async def test_a_held_draft_is_never_sent(cog):
    cog.api.would_post = False
    msg = _message()
    _route_to(cog, msg)

    await cog._handle_candidate(msg, supervised=False)
    await cog.poll_approved_drafts()

    msg.reply.assert_not_called()
    assert cog.api.rows[1]["status"] == "drafted"


# ---- a reply that went out counts even when record-post fails ----------------------------

def _approve(cog, *audit_ids):
    for audit_id in audit_ids:
        cog.api.rows[audit_id] = {"platform": "discord", "draft_text": DRAFT, "status": "approved",
                                  "target_url": f"https://discord.com/channels/1/{CHANNEL_ID}/{MSG_ID}"}


@pytest.mark.asyncio
async def test_a_sent_reply_ends_the_tick_when_record_post_fails(cog, caplog):
    msg = _message()
    _route_to(cog, msg)
    _approve(cog, 7, 8)
    cog.api.record_fails = True

    with caplog.at_level(logging.ERROR, logger="discord_bot"):
        await cog.poll_approved_drafts()

    msg.reply.assert_awaited_once()
    assert cog.api.rows[8]["status"] == "approved"
    assert "/social-outreach/reject/7" not in cog.api.calls
    assert any(
        r.levelno == logging.ERROR and "WAS SENT but record-post failed" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_an_unrecorded_reply_holds_the_gap_and_is_recorded_later(cog):
    msg = _message()
    _route_to(cog, msg)
    _approve(cog, 7, 8)
    cog.api.record_fails = True
    await cog.poll_approved_drafts()

    # The backend still knows of no post; the unrecorded reply holds the gap.
    await cog.poll_approved_drafts()
    msg.reply.assert_awaited_once()
    assert cog.api.rows[8]["status"] == "approved"

    # Record-post works again: the retry records the reply, and the backend's
    # own cadence now holds the gap.
    cog.api.record_fails = False
    await cog.poll_approved_drafts()
    msg.reply.assert_awaited_once()
    assert cog.api.rows[7]["status"] == "posted"
    assert cog.api.rows[8]["status"] == "approved"
    assert cog._unrecorded == []


@pytest.mark.asyncio
async def test_an_unrecorded_reply_counts_toward_the_daily_cap(cog):
    _approve(cog, 8)
    _route_to(cog, _message())
    cog._unrecorded = [{"payload": {"audit_id": i, "platform": "discord"}, "sent_at": time.time() - 3 * 3600}
                       for i in range(8)]
    cog.api.record_fails = True

    await cog.poll_approved_drafts()

    assert cog.api.rows[8]["status"] == "approved"


@pytest.mark.asyncio
async def test_an_unrecorded_reply_is_dropped_after_a_day(cog):
    cog._unrecorded = [{"payload": {"audit_id": 7, "platform": "discord"}, "sent_at": time.time() - 25 * 3600}]

    await cog.poll_approved_drafts()

    assert cog._unrecorded == []
    assert "/social-outreach/record-post" not in cog.api.calls
