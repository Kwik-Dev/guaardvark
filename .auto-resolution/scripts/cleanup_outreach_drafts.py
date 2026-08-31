#!/usr/bin/env python3
"""One-off: retire stale outreach drafts.

Marks every social_outreach_log row with status='drafted' as 'aborted' so it
drops out of the OutreachPage queue (which filters status='drafted'). The kill
switch has been OFF for >1 week, so these drafts were never going to post.

SAFE + REVERSIBLE: only flips status + sets abort_reason; draft_text is preserved,
and nothing in the posting path is touched. To restore: UPDATE social_outreach_log
SET status='drafted' WHERE abort_reason LIKE 'stale_cleanup_2026-06-01%'.
"""
from backend.app import app
from backend.models import SocialOutreachLog, db

REASON = "stale_cleanup_2026-06-01 (kill switch off >1wk; draft_text preserved; reversible)"

with app.app_context():
    rows = SocialOutreachLog.query.filter_by(status="drafted").all()
    n = len(rows)
    for r in rows:
        r.status = "aborted"
        r.abort_reason = REASON
    db.session.commit()
    print(f"Retired {n} stale 'drafted' rows -> 'aborted'.")
