"""
Posting gates shared by every path that can mark an outreach draft approved.

The drafter grades its own text, and that score leans toward whatever it just
wrote. A draft may post without a person's click only when the independent
check (external_grader) actually ran and passed. When that check could not
run, an unsupervised draft waits in the queue for approval instead of posting
on its self-grade. Supervised drafts wait for a person either way.

A self-share has no thread for the rubric to judge, so it is never graded and
always waits for a person, supervised or not.

Used by content_agent (Recon candidates) and POST /draft-comment (Reddit loop,
self-share, Discord cog, Outreach page).
"""

from __future__ import annotations

from backend.services.social_outreach import kill_switch


def independent_check_label(ext: dict) -> str:
    """passed, failed or unavailable: what the independent check concluded.

    ``ext`` is a grade_draft_externally result. Only ``checked`` counts as a
    check having run; a result without it is unavailable. A check that ran
    passed only when its ``passed`` is True: the draft engages with the thread,
    is on topic and has the right tone. Its 0-1 grade is for display and does
    not decide; a checked draft that did not pass may not post on its own,
    whatever its self-grade.
    """
    ext = ext or {}
    if not ext.get("checked"):
        return "unavailable"
    return "passed" if ext.get("passed") is True else "failed"


def independent_ok(
    ext: dict,
    *,
    supervised: bool,
    action: str = "comment",
    relevance_unchecked: bool = False,
) -> tuple[bool, str]:
    """Whether the independent check lets this draft go forward, and why.

    ``action`` is the audit row's action; a "share" is decided before ``ext``
    is read, because no check applies to it. ``relevance_unchecked`` says the
    thread-fit judge (external_grader.score_thread_relevance) could not run on
    the thread; a draft for it counts as unchecked even when its check passed.

    Returns one of:
      (True,  "passed")               checked and passed (independent_check_label)
      (False, "failed")               checked and did not pass
      (True,  "human_review")         unchecked, supervised: a person approves it;
                                      also every supervised share
      (False, "no_independent_check") unchecked, unsupervised: hold for approval
      (False, "relevance_unchecked")  checked and passed, but the thread-fit
                                      judge did not run; unsupervised: hold
      (True,  "check_not_required")   either of the two above, and the
                                      outreach_require_independent_check
                                      setting is switched off
      (False, "share_needs_person")   a share, unsupervised: hold for approval
                                      whatever that setting says
    """
    if action == "share":
        return (True, "human_review") if supervised else (False, "share_needs_person")
    label = independent_check_label(ext)
    if label == "failed":
        return False, "failed"
    if label == "passed" and not relevance_unchecked:
        return True, "passed"
    if supervised:
        return True, "human_review"
    if not kill_switch.requires_independent_check():
        return True, "check_not_required"
    return False, "no_independent_check" if label == "unavailable" else "relevance_unchecked"
