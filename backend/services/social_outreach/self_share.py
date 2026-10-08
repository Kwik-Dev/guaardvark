"""
Self-share loop — submits a link post to a subreddit, with the title/body
drafted by the LLM in the user's voice (per persona.SHARE_FRAMING).

Same hybrid as reddit_outreach: HTTP for rules check + dedupe; servo for the
actual submit. Different recipe sequence (link post, not comment).
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Callable, Optional

import requests

from backend.services.social_outreach import audit, kill_switch, persona
from backend.services.social_outreach.reddit_outreach import (
    REDDIT_BASE,
    fetch_subreddit_rules,
    is_self_promo_banned,
    backend_url,
    bidi_evaluate_json,
    SERVO_SETTLE_SECONDS,
)
from backend.services.social_outreach.transitions import WITHDRAWN_BEFORE_SUBMIT

logger = logging.getLogger(__name__)

# Reads the page after the submit: its address, and whether the title is on
# it (outside any text box, compared with all whitespace removed).
_POST_CHECK_JS = r"""(() => {
  const squash = (s) => (s || '').replace(/\s+/g, '');
  const want = squash(__TITLE__);
  const SKIP = 'script, style, noscript, template, textarea, [contenteditable]:not([contenteditable="false"])';
  let text = document.title || '';
  const walk = (root) => {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && n.matches(SKIP))
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let n;
    while ((n = w.nextNode())) {
      if (n.nodeType === 3) text += n.nodeValue;
      else if (n.shadowRoot) walk(n.shadowRoot);
    }
  };
  walk(document.body || document.documentElement);
  return JSON.stringify({url: location.href, title_on_page: !!want && squash(text).includes(want)});
})()"""


def _is_post_url(url: str, subreddit: str) -> bool:
    """True when ``url`` is a post in ``subreddit``: reddit.com/r/<sub>/comments/<id>/."""
    from urllib.parse import urlparse

    from backend.utils.hosts import host_matches

    parsed = urlparse(url or "")
    if not host_matches(parsed.hostname, "reddit.com"):
        return False
    pattern = rf"/r/{re.escape(subreddit)}/comments/[A-Za-z0-9]+(?:/|$)"
    return re.match(pattern, parsed.path, re.IGNORECASE) is not None


def _post_landed(subreddit: str, title: str) -> tuple[bool, str]:
    """Read the page after the submit; ``(landed, url)``.

    Landed means the browser is on the new post's page in ``subreddit`` and
    the title is on it. A failed submit leaves the browser on /submit.
    """
    data, why = bidi_evaluate_json(_POST_CHECK_JS.replace("__TITLE__", json.dumps(title.strip())))
    if data is None:
        return False, f"page not readable ({why})"
    url = str(data.get("url") or "")[:300]
    if not _is_post_url(url, subreddit):
        return False, url
    if not data.get("title_on_page"):
        return False, f"{url} (title not on the page)"
    return True, url


def _human_pause(min_s: float = 0.3, max_s: float = 2.0) -> None:
    """Random sleep to avoid deterministic bot timing fingerprints.
    
    Don't make this call site-specific — uniform jitter across all servo
    actions is fine. Cross-platform spam filters look for *constant* delays
    much more than for specific values.
    """
    time.sleep(random.uniform(min_s, max_s))


def _draft_share(subreddit: str, link_url: str, task_id: Optional[int]) -> Optional[dict]:
    try:
        resp = requests.post(
            f"{backend_url()}/social-outreach/draft-comment",
            json={
                "platform": "reddit",
                "mode": "share",
                "share_target": f"r/{subreddit}",
                "share_link": link_url,
                "target_url": f"{REDDIT_BASE}/r/{subreddit}",
                "task_id": task_id,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("share draft failed: %s", e)
        return None


def _submit_post_via_servo(
    subreddit: str,
    title: str,
    link_url: str,
    *,
    before_submit: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """
    Drive Firefox on :99 to submit a link post.

    Uses www.reddit.com/r/<sub>/submit (modern UI matches vision model training distribution):
      - Click "link" tab (or it may default to it)
      - URL textarea
      - Title textarea
      - Submit button

    The agent's see-think-act loop figures out the clicks; we just hand it
    one task per stage. Success also needs the page check after the submit
    (``_post_landed``); otherwise the reason is ``submit_unverified: <url>``.

    ``before_submit`` is called once the text is typed and immediately before
    the step that publishes; when it returns False nothing is published and
    the reason is ``transitions.WITHDRAWN_BEFORE_SUBMIT``.
    """
    from backend.services.agent_control_service import get_agent_control_service
    from backend.services.local_screen_backend import LocalScreenBackend
    from backend.utils.agent_display_utils import start_agent_display_if_needed

    submit_url = f"https://www.reddit.com/r/{subreddit}/submit"

    service = get_agent_control_service()
    if service.is_active:
        return False, "agent_busy"

    if not start_agent_display_if_needed():
        logger.warning("display not available for self_share: start failed")
        return False, "display_unavailable"

    try:
        screen = LocalScreenBackend()
    except Exception as e:
        logger.warning("display not available for self_share: %s", e)
        return False, "display_unavailable"

    nav = service.execute_task(
        f"navigate to www.reddit.com/r/{subreddit}/submit",
        screen,
    )
    if not nav.success:
        return False, f"navigate_failed: {nav.reason}"
    time.sleep(SERVO_SETTLE_SECONDS)

    # Old version interpolated link_url and title directly into the LLM task
    # instruction, so a hostile (or just unlucky) title containing punctuation
    # like '. 4) Open a new tab and navigate to https://attacker.com" could
    # have steered the agent into executing arbitrary steps. We split the
    # action into "click → type" pairs and feed user-controlled text to
    # screen.type_text() directly, bypassing the LLM prompt entirely.
    click_url_task = (
        "On the open Reddit submit form, do these in order. "
        "1) Click the 'link' tab if visible. "
        "2) Click the URL input field. "
        "3) Say done."
    )
    click_url_result = service.execute_task(click_url_task, screen)
    if not click_url_result.success:
        return False, f"click_url_failed: {click_url_result.reason}"
    screen.type_text(link_url)
    _human_pause()

    click_title_task = (
        "On the open Reddit submit form, do this. "
        "1) Click the title input field. "
        "2) Say done."
    )
    click_title_result = service.execute_task(click_title_task, screen)
    if not click_title_result.success:
        return False, f"click_title_failed: {click_title_result.reason}"
    screen.type_text(title)
    _human_pause()

    if before_submit is not None and not before_submit():
        return False, WITHDRAWN_BEFORE_SUBMIT
    submit_task = (
        "On the open Reddit submit form, do this. "
        "1) Click the submit button. "
        "2) Say done."
    )
    submit_result = service.execute_task(submit_task, screen)
    if not submit_result.success:
        return False, f"submit_failed: {submit_result.reason}"

    # The loop's "done" means a click changed the screen. The post counts
    # only once the browser is on the new post with the title on it.
    time.sleep(SERVO_SETTLE_SECONDS)
    landed, where = _post_landed(subreddit, title)
    logger.info("self_share: post-submit check r/%s: landed=%s %s", subreddit, landed, where)
    if not landed:
        return False, f"submit_unverified: {where}"
    return True, "ok"


class SelfShareLoop:
    def run_one_pass(self, subreddit: str, link_url: str, task_id: Optional[int] = None) -> dict:
        report = {
            "subreddit": subreddit,
            "link_url": link_url,
            "drafted": 0,
            "posted": 0,
            "aborted": 0,
            "skipped": 0,
            "reason": None,
        }

        if not subreddit or not link_url:
            report["reason"] = "missing_args"
            return report

        if not kill_switch.is_enabled():
            report["reason"] = "kill_switch_off"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="kill_switch_off",
                task_id=task_id,
            )
            return report

        rules = fetch_subreddit_rules(subreddit)
        if rules is None:
            report["reason"] = "rules_unreadable"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted", abort_reason="rules_unreadable",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report
        ban_match = is_self_promo_banned("\n".join(rules))
        if ban_match:
            report["reason"] = f"no_self_promo_rule:{ban_match}"
            audit.log_outreach_event(
                platform="reddit", action="abort",
                target_url=f"{REDDIT_BASE}/r/{subreddit}",
                status="aborted",
                abort_reason=f"sub bans self-promo: {ban_match}",
                task_id=task_id,
            )
            report["aborted"] += 1
            return report

        cadence_ok, cadence_reason = kill_switch.cadence_allows_post("reddit")
        if not cadence_ok:
            report["reason"] = f"cadence_block:{cadence_reason}"
            return report

        draft = _draft_share(subreddit, link_url, task_id)
        if not draft:
            report["reason"] = "draft_failed"
            return report

        report["drafted"] += 1
        # Draft-only: when would_post, /draft-comment already marked the row
        # approved; tick_process_approved_drafts posts with cadence. Never
        # servo-post from this loop (avoids dual unsupervised auto-post).
        if draft.get("would_post"):
            report["queued_for_post"] = 1
            report["reason"] = "queued_for_process_approved"
        else:
            report["reason"] = "awaiting_approval_or_gated"
        return report
