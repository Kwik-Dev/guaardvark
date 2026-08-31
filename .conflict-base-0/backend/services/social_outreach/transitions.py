"""Legal status transitions for social outreach draft rows."""

from __future__ import annotations

# Approve only from drafted (human or unsupervised auto-approve path).
APPROVE_FROM = frozenset({"drafted"})

# Reject may cancel work that has not yet posted.
REJECT_FROM = frozenset({"candidate", "drafted", "approved", "processing"})

# Claim for posting (Celery tick / Discord cog).
CLAIM_FROM = frozenset({"approved"})


def can_approve(status: str | None) -> bool:
    return (status or "") in APPROVE_FROM


def can_reject(status: str | None) -> bool:
    return (status or "") in REJECT_FROM


def can_claim(status: str | None) -> bool:
    return (status or "") in CLAIM_FROM
