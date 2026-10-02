"""Which web pages may call this backend from another origin.

A browser lets a page read a reply from another origin only when the reply
names that page's origin (CORS), and python-engineio refuses a Socket.IO
connection whose Origin header it does not accept. Flask's CORS, Socket.IO
and the sign-in cookie's origin check (backend/utils/api_session.py) all take
their list from here: this install's own pages, and nothing else.

This install's origins are its frontend port (VITE_PORT, default 5173) and its
backend port (FLASK_PORT, default 5000), over http and https, on each name and
address of this machine:

- localhost, 127.0.0.1 and [::1];
- every IPv4 address of its network interfaces (LAN, VPN, container bridge),
  since the dev and preview servers listen on all of them;
- its hostname, the hostname's first label, and "<first label>.local", the
  names start.sh and frontend/vite.config.js let the frontend be served under;
- the exact names in VITE_ALLOWED_HOSTS.

The frontend port is the web UI. The backend port is there for Python
Socket.IO clients (the CLI, the Discord plugin, the cluster bridge): their
websocket library sends the address it connected to as the Origin.

VITE_FRONTEND_URL and the comma-separated origins in GUAARDVARK_CORS_ORIGINS
are added to the frontend's, for a reverse proxy or for a name this machine is
reached by that the list above does not know.

The web UI normally calls a relative /api that its dev or preview server (or
Docker's nginx) passes to Flask, so for the browser those requests are
same-origin and CORS never applies to them; Socket.IO still checks their Origin.

The Interconnector is the one browser caller from other machines: a client
node's Settings page calls its master's status, register and heartbeat routes
directly (frontend/src/api/interconnectorService.js), from whatever origin
that node's UI has. Those three routes, and only those, also accept any
private-network or loopback origin. Register and heartbeat check the master's
Interconnector key; status says only the node's name, mode and sync settings.

The same answer decides which pages may send a state-changing request at all
(backend/utils/cross_site_guard.py), since CORS alone only stops a page from
reading the reply. The same names decide which Host a request may be
addressed to (own_host_names, used by backend/utils/host_check.py), which is
what stops a page whose DNS name was re-pointed at this machine.

Interface addresses and the hostname are read once per process, so a machine
that moves to a new address needs a backend restart before a browser on the
new address can use Socket.IO.
"""

from __future__ import annotations

import logging
import os
import re
import socket
from functools import lru_cache
from typing import Iterable, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

EXTRA_ORIGINS_ENV = "GUAARDVARK_CORS_ORIGINS"
DEFAULT_VITE_PORT = "5173"
DEFAULT_FLASK_PORT = "5000"
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SCHEMES = ("http", "https")

# The master routes a client node's Settings page calls from the browser.
NODE_ROUTES = r"/api/interconnector/(?:status|nodes/register|nodes/[^/]+/heartbeat)/?"
_PRIVATE_IPV4 = (
    r"(?:10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})"
)
_LOOPBACK = r"(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])"
NODE_ORIGIN = re.compile(
    rf"https?://(?:{_PRIVATE_IPV4}|{_LOOPBACK})(?::\d{{1,5}})?\Z", re.IGNORECASE
)
_NODE_ROUTE = re.compile(rf"^{NODE_ROUTES}\Z")

CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Content-Type", "Authorization", "X-API-Key"]

_warned: set[str] = set()


def normalize_origin(value: Optional[str]) -> Optional[str]:
    """``scheme://host[:port]`` as a browser writes it in an Origin header
    (lower case, no default port, no path), or None when ``value`` is not an
    http(s) URL."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if scheme not in _SCHEMES or not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _port(*names: str, default: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def vite_port() -> str:
    return _port("VITE_PORT", default=DEFAULT_VITE_PORT)


def flask_port() -> str:
    return _port("FLASK_PORT", "PORT", default=DEFAULT_FLASK_PORT)


def _interface_addresses() -> set[str]:
    try:
        import psutil

        return {
            addr.address
            for addrs in psutil.net_if_addrs().values()
            for addr in addrs
            if addr.family == socket.AF_INET and addr.address
        }
    except Exception:
        # Without psutil: the address the machine would use to reach the
        # outside. A UDP connect only picks a route; nothing is sent.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                return {probe.getsockname()[0]}
        except OSError:
            return set()


@lru_cache(maxsize=1)
def _machine_hosts() -> frozenset[str]:
    """Names and addresses a browser can use for this machine."""
    hosts = {"localhost", "127.0.0.1", "::1"}
    hosts.update(_interface_addresses())
    try:
        hostname = socket.gethostname().strip().lower()
    except OSError:
        hostname = ""
    if hostname:
        first = hostname.split(".", 1)[0]
        hosts.update({hostname, first, f"{first}.local"})
    return frozenset(hosts)


def _vite_allowed_hosts() -> list[str]:
    entries = (os.environ.get("VITE_ALLOWED_HOSTS") or "").split(",")
    return [name for name in (entry.strip().lower() for entry in entries) if name]


def _allowed_host_names() -> set[str]:
    """Exact names from VITE_ALLOWED_HOSTS. "all" and ".suffix" entries widen
    the dev server's Host check but name no machine, so they add no origin."""
    return {name for name in _vite_allowed_hosts() if name != "all" and not name.startswith(".")}


def allowed_host_suffixes() -> tuple[str, ...]:
    """The ".example.lan" entries of VITE_ALLOWED_HOSTS, each of which Vite
    reads as that name and every name under it."""
    return tuple(name for name in _vite_allowed_hosts() if name.startswith(".") and len(name) > 1)


def any_host_allowed() -> bool:
    """True when VITE_ALLOWED_HOSTS includes "all", which turns off Vite's
    Host check and the backend's (backend/utils/host_check.py)."""
    return "all" in _vite_allowed_hosts()


def url_host(value: Optional[str]) -> Optional[str]:
    """The host of an absolute http(s) URL, lower case, an IPv6 address
    without brackets; None for a relative path or anything else."""
    origin = normalize_origin(value)
    if origin is None:
        return None
    host = urlsplit(origin).hostname
    return host or None


def own_host_names() -> set[str]:
    """The names this install answers to by name (the backend's Host check,
    backend/utils/host_check.py, also accepts any IP address and the
    VITE_ALLOWED_HOSTS suffixes): this machine's names, the exact names in
    VITE_ALLOWED_HOSTS, and the hosts of VITE_FRONTEND_URL, of an absolute
    VITE_API_BASE_URL or VITE_SOCKET_URL (a build that calls the backend
    directly), and of each origin in GUAARDVARK_CORS_ORIGINS."""
    names = set(_machine_hosts()) | _allowed_host_names()
    for name in ("VITE_FRONTEND_URL", "VITE_API_BASE_URL", "VITE_SOCKET_URL"):
        host = url_host(os.environ.get(name))
        if host:
            names.add(host)
    for origin in extra_origins():
        host = url_host(origin)
        if host:
            names.add(host)
    return names


def _host_origins(hosts: Iterable[str], port: str) -> set[str]:
    origins = set()
    for host in hosts:
        literal = f"[{host}]" if ":" in host and not host.startswith("[") else host
        for scheme in _SCHEMES:
            origin = normalize_origin(f"{scheme}://{literal}:{port}")
            if origin:
                origins.add(origin)
    return origins


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        logger.warning(message)


def extra_origins() -> list[str]:
    """The origins GUAARDVARK_CORS_ORIGINS adds, normalized."""
    origins = []
    for entry in (os.environ.get(EXTRA_ORIGINS_ENV) or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        origin = normalize_origin(entry)
        if origin is None:
            _warn_once(
                f"{EXTRA_ORIGINS_ENV}: ignoring {entry!r}; each entry is an origin "
                "such as https://guaardvark.example or http://192.168.1.20:8080"
            )
            continue
        origins.append(origin)
    return origins


def frontend_origins() -> set[str]:
    """Origins of this install's web UI."""
    hosts = set(_machine_hosts()) | _allowed_host_names()
    origins = _host_origins(hosts, vite_port())
    configured = normalize_origin(os.environ.get("VITE_FRONTEND_URL"))
    if configured:
        origins.add(configured)
    origins.update(extra_origins())
    return origins


def allowed_origins() -> list[str]:
    """Every origin Flask's CORS and Socket.IO accept on every route."""
    hosts = set(_machine_hosts()) | _allowed_host_names()
    return sorted(frontend_origins() | _host_origins(hosts, flask_port()))


def origin_allowed(origin: Optional[str]) -> bool:
    normalized = normalize_origin(origin)
    return normalized is not None and normalized in allowed_origins()


def origin_allowed_on(origin: Optional[str], path: str) -> bool:
    """What Flask's CORS answers a page at ``origin`` calling ``path``: yes for
    this install's origins, and on the Interconnector's node routes also for
    private-network and loopback origins."""
    normalized = normalize_origin(origin)
    if normalized is None:
        return False
    if normalized in allowed_origins():
        return True
    return bool(_NODE_ROUTE.match(path or "")) and bool(NODE_ORIGIN.match(normalized))


def socketio_origin_allowed(origin: Optional[str], environ: Optional[dict] = None) -> bool:
    """python-engineio's cors_allowed_origins callable."""
    return origin_allowed(origin)


def flask_cors_resources() -> dict:
    """Flask-CORS resources: the Interconnector's node routes and everything
    else, as two patterns no path matches both of, so the order Flask-CORS
    tries them in does not matter."""
    own = allowed_origins()
    return {
        rf"^{NODE_ROUTES}\Z": {"origins": own + [NODE_ORIGIN]},
        rf"^(?!{NODE_ROUTES}\Z).*": {"origins": own},
    }


def init_cors(app, cors=None) -> list[str]:
    """Apply this policy to ``app`` with Flask-CORS; returns the origins.

    Credentials are allowed so a frontend on a separate origin (a build with
    an absolute VITE_API_BASE_URL) carries its sign-in cookie; api_session
    accepts that cookie only from frontend_origins(). The node routes answer
    other private-network pages with credentials allowed too, which gives
    them nothing: the cookie is SameSite=Strict, refused from any other
    origin, and those routes do not read it. A reply to a request with no
    Origin header carries no CORS headers.
    """
    if cors is None:
        from flask_cors import CORS as cors
    cors(
        app,
        resources=flask_cors_resources(),
        supports_credentials=True,
        always_send=False,
        allow_headers=CORS_HEADERS,
        methods=CORS_METHODS,
    )
    return allowed_origins()
