# backend/utils/auth_guard.py
"""Lightweight endpoint protection for dangerous operations.

When GUAARDVARK_API_KEY is set in the environment, protected endpoints
require the key in the X-API-Key header. When unset, localhost requests
pass freely but remote hosts are blocked from sensitive endpoints.
"""

import os
import hmac
import logging

from flask import request, jsonify

logger = logging.getLogger(__name__)

# Endpoints that always require protection (any method)
PROTECTED_PREFIXES = (
    '/api/code-execution/',
    '/api/backups/restore',
    '/api/backups/create',
    '/api/self-code/',
    # Social outreach has kill switches, draft approval, and fetch-meta — none of
    # which should be reachable from another machine on the LAN without an API key.
    '/api/social-outreach/',
)

# File APIs include both the document library and the live repository editor.
# Keep read-only document browser GETs public for the local UI, but protect
# server filesystem reads and every mutation-capable file route.
PROTECTED_FILE_PREFIXES = (
    '/api/files/read',
    '/api/files/list',
    '/api/files/write',
    '/api/files/create',
    '/api/files/delete',
    '/api/files/mkdir',
    '/api/files/rename',
    '/api/files/browse-server',
)

# Endpoints protected only on DELETE
PROTECTED_DELETE_PREFIXES = (
    '/api/backups/',
)

# Explicitly safe operations that are exempt from the host check even though they
# live under an otherwise-protected prefix. /api/meta is shared by many blueprints
# (jobs, index management, diagnostics) that MUST stay protected, but clearing
# __pycache__ is a non-destructive maintenance op (regenerable .pyc only; the
# module-purge that could destabilize the server is disabled) that the operator
# wants reachable from the LAN UI. Keep this list tiny and genuinely harmless.
SAFE_EXEMPT_PREFIXES = (
    '/api/meta/clear-pycache',
)

# Mutation-only protection: GET/HEAD/OPTIONS stay public for the local UI, but any
# non-GET (create/cancel/delete/run) requires auth/localhost — same model as the
# /api/memory hardening. Stops a random LAN host from wiping jobs/tasks/schedules.
MUTATION_PROTECTED_PREFIXES = (
    '/api/memory',
    '/api/tasks',
    '/api/scheduler',
    '/api/jobs',
    '/api/meta',
    '/api/progress-test',
)


def _is_localhost(addr):
    """Check if address is a loopback/localhost address."""
    return addr in ('127.0.0.1', '::1', 'localhost')


def _effective_client_ip():
    """Real client IP, accounting for the trusted local Vite proxy.

    `start.sh` serves the production UI via `vite preview`, whose proxy forwards
    /api and /socket.io to Flask from 127.0.0.1 — so a LAN device's request would
    otherwise look local and bypass this guard entirely. The Vite proxy sets
    X-Forwarded-For (xfwd) with the originating client. We trust that header ONLY
    when the direct TCP peer is loopback (i.e. it came through our own local
    proxy). A LAN attacker connecting straight to the backend port has a
    non-loopback peer, so a forged X-Forwarded-For from them is ignored.
    """
    peer = request.remote_addr
    if peer in ('127.0.0.1', '::1'):
        xff = request.headers.get('X-Forwarded-For', '')
        if xff:
            # Leftmost entry is the original client.
            return xff.split(',')[0].strip()
    return peer


def _is_protected():
    """Check if the current request targets a protected endpoint."""
    path = request.path
    for prefix in SAFE_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return False
    for prefix in PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return True
    for prefix in PROTECTED_FILE_PREFIXES:
        if path.startswith(prefix):
            return True
    if path.startswith('/api/files/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return True
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        for prefix in MUTATION_PROTECTED_PREFIXES:
            if path.startswith(prefix):
                return True
    if request.method == 'DELETE':
        for prefix in PROTECTED_DELETE_PREFIXES:
            if path.startswith(prefix):
                return True
    return False


def check_endpoint_auth():
    """Flask before_request hook: enforce auth on dangerous endpoints.

    Logic:
    - If endpoint is not protected → allow
    - If GUAARDVARK_API_KEY is set → require X-API-Key header (any host)
    - If GUAARDVARK_API_KEY is NOT set → allow localhost, block remote
    """
    if not _is_protected():
        return None

    api_key = os.environ.get('GUAARDVARK_API_KEY')

    if not api_key:
        # No key configured — localhost-only access. Use the effective client IP
        # so a LAN device proxied through the local Vite preview is still treated
        # as remote (the proxy makes request.remote_addr loopback otherwise).
        client_ip = _effective_client_ip()
        if _is_localhost(client_ip):
            return None
        logger.warning(
            f"[AUTH] Blocked remote access to {request.path} from {client_ip}"
        )
        return jsonify({"error": "Access denied from remote host"}), 403

    # API key is configured — require it
    provided_key = request.headers.get('X-API-Key', '')
    if provided_key and hmac.compare_digest(provided_key, api_key):
        return None

    logger.warning(
        f"[AUTH] Invalid/missing API key for {request.path} from {request.remote_addr}"
    )
    return jsonify({"error": "Invalid or missing API key"}), 401
