"""Answer only requests addressed to one of this install's own names.

A hostile site can point its DNS name at this machine's address after its page
has loaded (DNS rebinding). The browser then sends that page's requests here
and treats them as same-origin with the page: they carry the hostile name as
their Origin, and the page reads every reply. No Origin or CORS check can tell
them from this install's own pages, and the page is on the same machine as the
backend, which trusts it. What the page cannot change is the Host header,
which names the hostile site. So a request whose Host is not one of this
install's names is refused with 421 before Socket.IO or any view runs.

The frontend port has the same protection from Vite's allowedHosts
(frontend/vite.config.js); this is the backend port's.

Accepted, whatever the port:

- an IP address (IPv4, or IPv6 in brackets). A browser sends one only when the
  page's own address is that IP, so no name was looked up that could have been
  re-pointed. This is how the web UI reaches the backend (its dev and preview
  servers proxy to 127.0.0.1:FLASK_PORT), how Interconnector and cluster nodes
  call a node they know by address, and how Docker's published port and an
  address this machine gains after start are reached;
- the names in cors_policy.own_host_names(): localhost, this machine's
  hostname, the hostname's first label and "<first label>.local", the exact
  names in VITE_ALLOWED_HOSTS, and the hosts of VITE_FRONTEND_URL, an absolute
  VITE_API_BASE_URL or VITE_SOCKET_URL, and each origin in
  GUAARDVARK_CORS_ORIGINS;
- a name under a ".example.lan" entry of VITE_ALLOWED_HOSTS, as Vite reads it.

VITE_ALLOWED_HOSTS=all turns the check off, as it turns off Vite's: the
frontend then answers any name and passes its requests on to the backend.
A request without a Host header passes; browsers always send one.

Every other HTTP server Guaardvark starts applies the same rule through the
adapters below, since a page re-pointed at 127.0.0.1 reaches those ports as
easily as this one: the plugin servers (Audio Foundry, upscaling, swarm,
video editor, vision pipeline, GPU embedding, the Discord bot's health port),
the ComfyUI Guaardvark launches (plugins/comfyui/guaardvark_nodes/), the MCP
server's HTTP transport and the reboot log server. Plugin servers cannot
import the backend package, so they load this file through
backend/utils/sidecar_guard.py as a member of a stand-in package. It imports
only the standard library and cors_policy, relatively, and must stay that way.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Optional

from . import cors_policy

logger = logging.getLogger(__name__)

HOST_CODE = "host_not_allowed"
STATUS = "421 Misdirected Request"
_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")


def host_name(host_header: Optional[str]) -> Optional[str]:
    """The name or address in a Host header, lower case, without its port or
    a final dot, IPv6 in brackets; None when it is neither a host name nor an
    IP address."""
    text = (host_header or "").strip().lower()
    if not text:
        return None
    if text.startswith("["):
        end = text.find("]")
        rest = text[end + 1:]
        if end < 0 or (rest and not re.fullmatch(r":\d{1,5}", rest)):
            return None
        try:
            ipaddress.IPv6Address(text[1:end].split("%", 1)[0])
        except ValueError:
            return None
        return text[:end + 1]
    name, _, port = text.partition(":")
    if port and not port.isdigit():
        return None
    name = name[:-1] if name.endswith(".") else name
    if not name or len(name) > 253:
        return None
    if not all(_LABEL.match(label) for label in name.split(".")):
        return None
    return name


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        return False


def host_allowed(host_header: Optional[str]) -> bool:
    """Whether this install answers a request with this Host header."""
    if host_header is None or cors_policy.any_host_allowed():
        return True
    name = host_name(host_header)
    if name is None:
        return False
    if _is_ip(name):
        return True
    own = {n[:-1] if n.endswith(".") else n for n in cors_policy.own_host_names()}
    if name in own:
        return True
    return any(name == suffix[1:] or name.endswith(suffix) for suffix in cors_policy.allowed_host_suffixes())


def refusal_message(host_header: str, scheme: str = "http", forwarded_host: Optional[str] = None) -> str:
    """What to do about a refused Host. ``forwarded_host`` is a proxy's
    X-Forwarded-Host; when it names the same host, its port (the one the
    browser used, which Docker's nginx keeps and its Host drops) goes in the
    address to add."""
    name = host_name(host_header)
    if name is None:
        return "Refused: the host name this request was sent to is not valid."
    shown = host_header
    if forwarded_host and host_name(forwarded_host) == name:
        shown = forwarded_host
    address = f"{scheme}://{shown.strip().lower()}"
    return (
        f"Refused: this Guaardvark does not answer to the name {name}. If this machine "
        f"is reached by that name, add {address} to {cors_policy.EXTRA_ORIGINS_ENV} in "
        ".env (comma-separated) and restart Guaardvark."
    )


def refusal_body(host_header: Optional[str], scheme: str = "http", forwarded_host: Optional[str] = None) -> bytes:
    """The JSON body of the 421 every adapter below sends."""
    message = refusal_message(host_header or "", scheme, forwarded_host or None)
    return json.dumps({"error": message, "code": HOST_CODE}).encode()


def single_host(values) -> Optional[str]:
    """The Host header among all the values a request carried: None without
    one, and "" (never allowed) for more than one, since which of two a
    server would honour is not something to guess."""
    values = list(values)
    if not values:
        return None
    return values[0] if len(values) == 1 else ""


def _log_refusal(method, path, client, host_header) -> None:
    logger.warning(
        "[HOST] Refused %s %s from %s addressed to %r",
        method, path, client, (host_header or "")[:100],
    )


class HostCheckMiddleware:
    """WSGI middleware applying host_allowed to every request, Socket.IO's
    included; wrap app.wsgi_app with it after socketio.init_app."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        host_header = environ.get("HTTP_HOST")
        if host_allowed(host_header):
            return self.wsgi_app(environ, start_response)
        forwarded = (environ.get("HTTP_X_FORWARDED_PROTO") or "").split(",")[0].strip().lower()
        scheme = forwarded if forwarded in ("http", "https") else environ.get("wsgi.url_scheme", "http")
        _log_refusal(environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"), environ.get("REMOTE_ADDR"), host_header)
        forwarded_host = (environ.get("HTTP_X_FORWARDED_HOST") or "").split(",")[0].strip()
        body = refusal_body(host_header, scheme, forwarded_host)
        start_response(STATUS, [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
        return [body]


class HostCheckASGIMiddleware:
    """ASGI middleware applying host_allowed to every HTTP request and
    websocket: ``app.add_middleware(HostCheckASGIMiddleware)`` on FastAPI or
    Starlette, or wrap any ASGI app in it. A refused websocket is closed
    before it is accepted, which the server answers with 403."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or ()
        host_header = single_host(v.decode("latin-1") for k, v in headers if k.lower() == b"host")
        if host_allowed(host_header):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        _log_refusal(scope.get("method", "WEBSOCKET"), scope.get("path"), client[0] if client else None, host_header)
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        forwarded_host = next(
            (v.decode("latin-1").split(",")[0].strip() for k, v in headers if k.lower() == b"x-forwarded-host"),
            None,
        )
        scheme = "https" if scope.get("scheme") == "https" else "http"
        body = refusal_body(host_header, scheme, forwarded_host)
        await send({
            "type": "http.response.start",
            "status": 421,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def aiohttp_middleware():
    """An aiohttp middleware applying host_allowed to every request, websocket
    upgrades included. Put it first in the application's list so it runs
    before any other. aiohttp is imported only when this is called."""
    from aiohttp import web

    @web.middleware
    async def host_check(request, handler):
        host_header = single_host(request.headers.getall("Host", []))
        if host_allowed(host_header):
            return await handler(request)
        _log_refusal(request.method, request.path, request.remote, host_header)
        forwarded_host = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        return web.Response(
            status=421,
            body=refusal_body(host_header, request.scheme, forwarded_host),
            content_type="application/json",
        )

    return host_check
