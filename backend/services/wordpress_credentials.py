"""WordPress site keys, kept in the encrypted credential store.

A site's LLAMANATOR2 API key or WordPress Application Password is stored by
``backend.utils.credential_store`` under ``wordpress:site:<id>``, the same
store Connections use, and never in Postgres. The record also names the site
URL it was saved for, so a record left by an earlier database whose site ids
have been reused is never sent to a different site.

``wordpress_sites.api_key`` predates the store and is kept empty.
``migrate_database_keys`` moves what older installs left there, at startup;
until it has, readers fall back to the column.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.utils import credential_store
from backend.utils.plugin_secrets import is_masked_or_empty, mask_hint

logger = logging.getLogger(__name__)

SECRET_FIELD = "api_key"
URL_FIELD = "site_url"

MISSING_KEY_MESSAGE = (
    "No API key is stored for this WordPress site. Enter it again in the "
    "site's settings."
)


def site_ref(site_id: int) -> str:
    """Credential-store key for a WordPress site."""
    return credential_store.credential_ref("wordpress", "site", str(site_id))


def incoming_key(value: Any) -> Optional[str]:
    """The key a form sent, or None when it sent none.

    Blank values and a masked hint echoed back mean "keep the stored key", as
    on the Connections page.
    """
    if is_masked_or_empty(value):
        return None
    return value.strip()


def _stored_key(site) -> str:
    """The key the store holds for *site*'s URL, or ""."""
    if site.id is None:
        return ""
    bundle = credential_store.get_secret(site_ref(site.id))
    saved_for = bundle.get(URL_FIELD)
    if saved_for and saved_for != site.url:
        logger.warning(
            "Credential %s was saved for another site URL; not using it.",
            site_ref(site.id),
        )
        return ""
    return bundle.get(SECRET_FIELD) or ""


def get_api_key(site) -> str:
    """The key to send to *site*, or "" when none is stored."""
    return _stored_key(site) or (site.api_key or "")


def set_api_key(site, api_key: str) -> None:
    """Store *api_key* for *site* and empty its database column.

    The row must have an id (flush a new one first). Raises when the store
    cannot write, leaving the row as it was.
    """
    credential_store.set_secret(
        site_ref(site.id),
        {SECRET_FIELD: api_key, URL_FIELD: site.url},
        hint_field=SECRET_FIELD,
    )
    site.api_key = ""


def delete_api_key(site_id: int) -> bool:
    """Remove a site's stored key. A failure is logged, not raised: the row
    is already gone, and a record left behind is bound to that site's URL."""
    try:
        return credential_store.delete_secret(site_ref(site_id))
    except Exception as e:  # noqa: BLE001 - must not fail the caller's delete
        logger.warning("Could not remove credential %s: %s", site_ref(site_id), e)
        return False


def key_status(site) -> Dict[str, Any]:
    """Masked state for API responses. Never returns the key."""
    try:
        key = _stored_key(site)
        encrypted = bool(key) and credential_store.get_status(site_ref(site.id)).get("encrypted", False)
    except Exception:  # noqa: BLE001 - a broken store must not 500 the list
        key, encrypted = "", False
    if not key:
        key, encrypted = (site.api_key or "").strip(), False
    return {
        "configured": bool(key),
        "hint": mask_hint(key) if key else "",
        "encrypted": bool(encrypted),
    }


def migrate_database_keys() -> Dict[str, int]:
    """Move keys still held in ``wordpress_sites.api_key`` into the store.

    Safe to run at every start. Rows with an empty column are skipped. A
    column is emptied only after the store reads the same key back, so a
    failed write leaves the key in the database for the next start to retry.
    When the store already holds a key for the site's URL, that key was saved
    after the column's copy (restoring an older backup brings the column
    back), so it is kept and the column emptied. Nothing is written while the
    credential file exists but cannot be read, since a write would replace it.

    Needs an app context. Returns counts: moved, already_stored, failed.
    """
    from backend.models import WordPressSite, db

    result = {"moved": 0, "already_stored": 0, "failed": 0}
    pending = [
        site
        for site in WordPressSite.query.filter(WordPressSite.api_key != "").all()
        if (site.api_key or "").strip()
    ]
    if not pending:
        return result

    if not credential_store.file_readable():
        logger.error(
            "The credential file cannot be read, so %d WordPress site key(s) stay "
            "in the database until it can.",
            len(pending),
        )
        result["failed"] = len(pending)
        return result

    for site in pending:
        site_id = site.id
        try:
            legacy = site.api_key
            stored = _stored_key(site)
            if stored:
                if stored != legacy:
                    logger.warning(
                        "WordPress site %s: kept the key in the credential store and "
                        "dropped the older copy in the database.",
                        site_id,
                    )
                outcome = "already_stored"
            else:
                credential_store.set_secret(
                    site_ref(site_id),
                    {SECRET_FIELD: legacy, URL_FIELD: site.url},
                    hint_field=SECRET_FIELD,
                )
                if _stored_key(site) != legacy:
                    raise RuntimeError("the credential store did not read the key back")
                outcome = "moved"
            site.api_key = ""
            db.session.commit()
            result[outcome] += 1
        except Exception as e:  # noqa: BLE001 - one row must not stop the rest
            db.session.rollback()
            logger.error(
                "WordPress site %s: key stays in the database for now (%s).",
                site_id,
                e,
            )
            result["failed"] += 1
    return result
