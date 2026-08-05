"""Platform-agnostic poster — drive the general agent loop to post anywhere.

The founding principle: put the work into the hand/eye/brain and the platform
stops mattering. Modern models already know how Reddit, X, YouTube, and Facebook
compose boxes work; the grounded eye (DOM element inventory in the decision
prompt) tells the brain what's actually on THIS page. So instead of a hand-written
BiDi poster per platform, one NL-driven loop finds the composer, types the text,
and submits — on any site the operator is logged into.

Used for platforms without a dedicated calibrated fast-path (X/Twitter, Facebook).
Reddit and YouTube keep their existing BiDi posters for now; once this loop is
live-verified they can migrate here too, and "adding a platform" becomes "log in".

Contract mirrors reddit_outreach.post_comment_via_servo: returns (success, reason).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Words that, when a prominent element and NO composer is present, mean the
# cloned session is logged out on this platform.
_LOGIN_CTA_WORDS = ("log in", "sign in", "log-in", "sign-in", "login", "signin")


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _preflight_logged_in(platform: str) -> tuple[bool, str]:
    """Grounded-eye login check: is a composer present and no login wall?

    Uses the same DOM inventory that grounds the brain. Deterministic, no LLM.
    Conservative: only aborts on a CLEAR logged-out signal (a login CTA with no
    composer). If the page can't be introspected, proceeds and lets the post's
    own verification catch failure — better than never posting on pages we can't
    read.
    """
    try:
        from backend.services.dom_metadata_extractor import DOMMetadataExtractor
        snap = DOMMetadataExtractor.get_instance().extract()
    except Exception as e:  # noqa: BLE001
        logger.info("preflight: DOM introspection unavailable (%s) — proceeding", e)
        return True, "preflight_skipped_no_dom"

    if not snap or not getattr(snap, "success", False) or not snap.elements:
        return True, "preflight_skipped_no_elements"

    def _is_composer(el) -> bool:
        et = (getattr(el, "element_type", "") or "").lower()
        tag = (getattr(el, "tag", "") or "").lower()
        return (
            et == "composer"
            or "textbox" in et
            or tag == "textarea"
            or (tag == "div" and "contenteditable" in et)
        )

    has_composer = any(_is_composer(el) for el in snap.elements)
    has_login_cta = any(
        any(w in (getattr(el, "text", "") or "").lower() for w in _LOGIN_CTA_WORDS)
        for el in snap.elements
    )

    if has_login_cta and not has_composer:
        return False, f"logged_out:{platform}"
    return True, "ok"


def post_via_agent_loop(
    platform: str,
    target_url: str,
    text: str,
    *,
    action: str = "comment",
    anchor_hint: Optional[str] = None,
) -> tuple[bool, str]:
    """Post `text` on `target_url` by driving the general see-think-act loop.

    action: "comment" | "reply" | "share" — shapes the NL instruction only; the
    loop discovers the actual controls. anchor_hint (for replies) names the
    parent comment to reply under.

    Prompt-injection safe: the user/draft text is NEVER interpolated into an LLM
    instruction. The loop is asked only to CLICK the composer and the submit
    control; the text is typed via screen.type_text(), bypassing the prompt (same
    hardening as self_share._submit_post_via_servo).
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed
    from backend.services.social_outreach.reddit_outreach import SERVO_SETTLE_SECONDS

    if not (text or "").strip():
        return False, "empty_text"

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"
    if not start_agent_display_if_needed():
        return False, "display_unavailable"
    try:
        screen = LocalScreenBackend()
    except Exception as e:  # noqa: BLE001
        logger.warning("general_poster: display unavailable: %s", e)
        return False, "display_unavailable"

    # 1) Navigate to the target.
    nav = service.execute_task(f"navigate to {target_url}", screen)
    if not nav.success:
        return False, f"navigate_failed: {nav.reason}"
    time.sleep(SERVO_SETTLE_SECONDS)

    # 2) Login preflight (grounded eye).
    ok, reason = _preflight_logged_in(platform)
    if not ok:
        return False, reason

    # 3) Focus the composer. For replies, first open the reply UI under the
    #    named parent. anchor_hint is a page LABEL, not free instruction — but
    #    keep it short and non-directive.
    if action == "reply" and anchor_hint:
        open_reply = (
            "On this page, find the comment that contains this text: "
            f"\"{anchor_hint[:120]}\". Click its Reply control to open a reply box, "
            "then say done."
        )
        r = service.execute_task(open_reply, screen)
        if not r.success:
            return False, f"open_reply_failed: {r.reason}"

    focus_task = (
        f"On this page, find the main text box for writing a {action} "
        "(a comment/reply/post composer — look at the interactive elements list). "
        "Click it so the cursor is inside it, then say done."
    )
    focus = service.execute_task(focus_task, screen)
    if not focus.success:
        return False, f"focus_composer_failed: {focus.reason}"

    # 4) Type the user text directly — never through the LLM prompt.
    screen.type_text(text)
    _human_pause()

    # 5) Submit.
    submit_task = (
        f"On this page, click the button that publishes/submits the {action} "
        "(e.g. Post, Reply, Comment, Tweet). Then say done."
    )
    submit = service.execute_task(submit_task, screen)
    if not submit.success:
        return False, f"submit_failed: {submit.reason}"

    return True, "ok"
