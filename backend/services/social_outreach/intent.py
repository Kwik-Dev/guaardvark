"""Natural-language outreach intent → structured plan + queued jobs.

Single entry used by HTTP /intent, chat tool outreach_execute_intent,
GUI slash freeform, and `llx outreach`.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.services.social_outreach import kill_switch
from backend.services.social_outreach.job_service import queue_outreach_run


_SHORT_VERBS = frozenset({
    "status", "reddit", "self_share", "self-share", "recon", "draft",
    "enable", "kill", "queue", "approve", "reject", "youtube", "discord",
})


def is_short_verb(token: str) -> bool:
    return (token or "").strip().lower().replace("-", "_") in {
        v.replace("-", "_") for v in _SHORT_VERBS
    }


def parse_outreach_intent(
    text: str,
    *,
    platform: Optional[str] = None,
    action: Optional[str] = None,
    topics: Optional[list[str]] = None,
    max_candidates: Optional[int] = None,
    chain_draft: Optional[bool] = None,
) -> dict[str, Any]:
    """Parse freeform text (and optional structured overrides) into a plan."""
    raw = (text or "").strip()
    plan: dict[str, Any] = {
        "raw_text": raw,
        "platform": (platform or "").strip().lower() or None,
        "action": (action or "").strip().lower() or None,
        "topics": [t.strip() for t in (topics or []) if t and str(t).strip()],
        "max_candidates": max(1, min(int(max_candidates or 5), 25)),
        "chain_draft": True if chain_draft is None else bool(chain_draft),
        "subreddit": None,
    }

    lower = raw.lower()

    if not plan["platform"]:
        if re.search(r"\byoutube\b|\byoutu\.be\b|\bvideo(s)?\b", lower):
            plan["platform"] = "youtube"
        elif re.search(r"\bdiscord\b", lower):
            plan["platform"] = "discord"
        elif re.search(r"\breddit\b|\br/\w+", lower):
            plan["platform"] = "reddit"
        else:
            plan["platform"] = "youtube"  # NL comment-on-content default

    if not plan["action"]:
        if re.search(r"\bshare\b|\blink.?post\b|\bself.?share\b", lower):
            plan["action"] = "share"
        elif re.search(r"\breply\b|\breplies\b", lower):
            plan["action"] = "reply"
        elif re.search(r"\brecon\b|\bscout\b|\bcandidate", lower):
            plan["action"] = "recon"
        elif re.search(r"\bdraft\b", lower) and not re.search(r"\bcomment\b", lower):
            plan["action"] = "draft"
        else:
            plan["action"] = "comment"

    m = re.search(r"\br/([A-Za-z0-9_]+)", raw)
    if m:
        plan["subreddit"] = m.group(1)

    if not plan["topics"]:
        plan["topics"] = _extract_topics(raw)

    if plan["action"] == "recon":
        plan["chain_draft"] = False if chain_draft is None else bool(chain_draft)
    elif plan["action"] in ("comment", "reply", "share"):
        if chain_draft is None:
            plan["chain_draft"] = True

    return plan


def _extract_topics(text: str) -> list[str]:
    """Pull topic phrases after regarding/about/on … for / or , / and."""
    raw = (text or "").strip()
    if not raw:
        return []

    # regarding X or Y / about X, Y / on X and Y
    m = re.search(
        r"(?:regarding|about|around|on(?:\s+the)?|for)\s+(.+)$",
        raw,
        re.IGNORECASE,
    )
    chunk = m.group(1).strip() if m else ""
    if not chunk:
        # Fallback: quoted phrases
        quotes = re.findall(r'["“](.+?)["”]', raw)
        if quotes:
            return [q.strip() for q in quotes if q.strip()]
        return []

    # Strip trailing filler
    chunk = re.sub(r"\b(please|thanks|thank you)\.?$", "", chunk, flags=re.I).strip()
    # Drop leading "some youtube videos" style leftovers if parser captured too much
    chunk = re.sub(
        r"^(?:some\s+)?(?:youtube\s+)?videos?\s+(?:regarding|about|on)\s+",
        "",
        chunk,
        flags=re.I,
    ).strip()

    parts = re.split(r"\s*(?:,|/|\bor\b|\band\b)\s*", chunk, flags=re.I)
    topics = []
    for p in parts:
        t = p.strip(" .;:")
        if not t or len(t) < 2:
            continue
        # Skip pure platform words
        if t.lower() in ("youtube", "reddit", "discord", "videos", "video", "comments"):
            continue
        topics.append(t)
    return topics[:8]


def execute_outreach_intent(
    text: str = "",
    *,
    platform: Optional[str] = None,
    action: Optional[str] = None,
    topics: Optional[list[str]] = None,
    max_candidates: Optional[int] = None,
    chain_draft: Optional[bool] = None,
    created_by: str = "intent",
) -> dict[str, Any]:
    """Parse intent and queue Task-backed recon (+ optional draft) jobs."""
    plan = parse_outreach_intent(
        text,
        platform=platform,
        action=action,
        topics=topics,
        max_candidates=max_candidates,
        chain_draft=chain_draft,
    )

    if not kill_switch.is_enabled():
        return {
            "ok": False,
            "error": "outreach is disabled (kill switch is off). Enable it from /outreach or the Outreach page first.",
            "plan": plan,
        }

    plat = plan["platform"]
    act = plan["action"]
    queued: list[dict[str, Any]] = []

    try:
        if plat == "youtube" and act in ("comment", "reply", "recon"):
            profiles = plan["topics"] or ["local AI"]
            queued.append(
                queue_outreach_run(
                    "youtube",
                    keyword_profiles=profiles,
                    batch_size=plan["max_candidates"],
                    chain_draft=bool(plan["chain_draft"]),
                    created_by=created_by,
                )
            )
        elif plat == "reddit" and act == "share":
            queued.append(
                queue_outreach_run(
                    "self_share",
                    subreddit=plan.get("subreddit"),
                    created_by=created_by,
                )
            )
        elif plat == "reddit" and act in ("comment", "recon"):
            queued.append(
                queue_outreach_run(
                    "recon",
                    subreddit=plan.get("subreddit"),
                    created_by=created_by,
                )
            )
            if plan["chain_draft"]:
                queued.append(
                    queue_outreach_run("draft", created_by=created_by)
                )
        elif act == "draft":
            queued.append(
                queue_outreach_run("draft", created_by=created_by)
            )
        else:
            return {
                "ok": False,
                "error": (
                    f"unsupported intent platform={plat!r} action={act!r}. "
                    "Try YouTube comment topics, Reddit recon/comment, or self-share."
                ),
                "plan": plan,
            }
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e), "plan": plan}

    task_ids = [q.get("task_id") for q in queued if q.get("task_id")]
    summary = (
        f"Queued outreach for {plat}/{act}"
        + (f" topics={plan['topics']}" if plan["topics"] else "")
        + f" → task(s) {task_ids}. "
        + ("Drafts will land in the Outreach queue for your approval (supervised)."
           if kill_switch.is_supervised() or plan["chain_draft"]
           else "Check the Outreach queue / Activity for progress.")
    )
    return {
        "ok": True,
        "plan": plan,
        "queued": queued,
        "task_ids": task_ids,
        "message": summary,
        "supervised": kill_switch.is_supervised(),
        "posts_require_approve": True,  # intent never bypasses approve/cadence
    }
