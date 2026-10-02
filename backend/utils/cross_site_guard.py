"""Refuse a state-changing request that a page on another site sent.

CORS stops a page from reading a reply, not from sending the request: a form,
a fetch in "no-cors" mode or sendBeacon on any site can make the browser POST
here. A route that trusts this machine (no API key yet) or a signed-in browser
would act on it, since the browser runs on this machine. So every request
other than GET, HEAD and OPTIONS is refused, before any view runs, when the
browser reports that it came from a page that is not this install's:

- its Origin is "null" (a sandboxed frame, a file:// page, a redirect that
  hid the origin) or not an origin at all;
- its Origin is one Flask's CORS would not answer on that path
  (cors_policy.origin_allowed_on, the Interconnector's node routes included),
  and the browser does not report the request as same-origin, either with
  Sec-Fetch-Site: same-origin or, for browsers that do not send that header,
  by Origin matching the address the request was sent to (Host, or the
  X-Forwarded-Host and -Proto a proxy in front of Flask adds);
- it has no Origin, but Sec-Fetch-Site says a page on another origin sent it
  (cross-site or same-site).

A page cannot set Origin, Host or any Sec-Fetch- header, and cannot add an
X-Forwarded- header to a request without a CORS preflight, which this
install answers only for its own origins; so none of these can be forged by
the page being refused. Requests with neither Origin nor Sec-Fetch-Site (the
CLI, the MCP server, curl, the Interconnector's and the cluster's calls
between machines) pass untouched, as do pages served from the address they
call, whatever name the machine was reached by.

GET and HEAD always pass, since any page can send them (an <img>, a link, a
no-cors fetch), so a route must never change anything on either. Flask sends
HEAD to the GET rule's view: a view that also takes POST or DELETE acts only
when request.method names that method, never in an "else" after checking for
GET (backend/tests/test_get_requests_change_nothing.py).
"""

from __future__ import annotations

import logging
from typing import Optional

from flask import jsonify, request

from backend.utils.cors_policy import EXTRA_ORIGINS_ENV, normalize_origin, origin_allowed_on

logger = logging.getLogger(__name__)

CROSS_SITE_CODE = "cross_site_request"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CROSS_SITE_MESSAGE = (
    "Refused: a page that is not this Guaardvark's own sent this request. "
    "If it is your Guaardvark opened under another address, add that address "
    f"to {EXTRA_ORIGINS_ENV} in .env and restart."
)


def _first(value: Optional[str]) -> str:
    return (value or "").split(",")[0].strip()


def _addressed_origins() -> set[str]:
    """The origin the browser sent this request to, as Flask sees it and as a
    proxy in front of Flask reports it."""
    origins = {normalize_origin(f"{request.scheme}://{request.host}")}
    forwarded_host = _first(request.headers.get("X-Forwarded-Host"))
    if forwarded_host:
        proto = _first(request.headers.get("X-Forwarded-Proto")) or request.scheme
        origins.add(normalize_origin(f"{proto}://{forwarded_host}"))
    origins.discard(None)
    return origins


def refusal_reason() -> Optional[str]:
    """Why the current request counts as sent by another site's page, or None."""
    if request.method in SAFE_METHODS:
        return None
    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    origin = request.headers.get("Origin")
    if origin is None:
        if site in ("cross-site", "same-site"):
            return f"no Origin, Sec-Fetch-Site: {site}"
        return None
    normalized = normalize_origin(origin)
    if normalized is None:
        return f"Origin {origin.strip()[:80]!r}"
    if site == "same-origin" or origin_allowed_on(normalized, request.path):
        return None
    if normalized in _addressed_origins():
        return None
    return f"Origin {normalized}"


def refuse_cross_site_request():
    """Flask before_request hook; register it ahead of the auth guard."""
    reason = refusal_reason()
    if reason is None:
        return None
    logger.warning(
        "[CROSS-SITE] Refused %s %s from %s (%s)",
        request.method, request.path, request.remote_addr, reason,
    )
    return jsonify({"error": CROSS_SITE_MESSAGE, "code": CROSS_SITE_CODE}), 403
