"""Guaardvark never sends logins from ~/.netrc (or $NETRC).

requests reads .netrc on every request and every redirect hop, and a
``default`` entry there matches any host. A probe of Hugging Face, a crawl of a
site someone added, or a webhook would otherwise carry the user's saved login
— replacing even an explicit token header — to whoever answers. No part of the
product relies on .netrc, so the lookup is switched off for the whole process.
Credentials a request passes explicitly are still sent, and proxy and CA
settings from the environment still apply.
"""
from __future__ import annotations


def install() -> None:
    try:
        import requests.sessions as sessions
    except ImportError:  # a process without requests has nothing to switch off
        return

    def _no_netrc(url, raise_errors=False):
        return None

    sessions.get_netrc_auth = _no_netrc
