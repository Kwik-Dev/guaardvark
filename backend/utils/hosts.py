"""Hostname matching for URL platform detection and allowlists."""

from __future__ import annotations

import functools
import ipaddress
import os
import socket
import threading
from urllib.parse import urlparse

from urllib3.exceptions import NewConnectionError


class PrivateAddressError(NewConnectionError):
    """A connection refused because the host resolved to a non-public address."""

    def __init__(self, conn, host: str, address: str):
        self.host_name = host
        self.address = address
        super().__init__(conn, f"{host} resolves to a private or local address ({address})")


def url_host(url: str | None) -> str:
    """Lower-cased hostname of ``url``, or "" when it has none."""
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def host_matches(host: str | None, *domains: str) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one.

    ``reddit.com`` matches ``reddit.com`` and ``old.reddit.com`` but not
    ``reddit.com.example.net`` or ``notreddit.com``.
    """
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    for domain in domains:
        d = domain.lower().rstrip(".")
        if h == d or h.endswith("." + d):
            return True
    return False


def url_host_matches(url: str | None, *domains: str) -> bool:
    """``host_matches`` applied to the hostname of ``url``."""
    return host_matches(url_host(url), *domains)


def private_address_reason(url: str | None) -> str | None:
    """Why ``url`` may not be fetched from this machine, or None when it may.

    Refused: anything but http(s), a URL without a host, a URL whose host two
    parsers read differently (a backslash in the authority, for one), and a host
    that resolves to any address that is not globally routable: loopback,
    private and CGNAT/Tailscale ranges, link-local, site-local, multicast,
    reserved. So a fetch tool cannot be pointed at this machine or its networks.
    An internationalized name is compared and looked up in its encoded
    ("xn--") form, the one the request is sent to.
    """
    if "\\" in (url or ""):
        return "URLs containing a backslash are refused"
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return "the URL could not be parsed"
    if parsed.scheme not in ("http", "https"):
        return "only http and https URLs can be fetched"
    host = parsed.hostname
    if not host:
        return "the URL has no host name"
    try:
        from urllib3.util import parse_url
        sent_to = (parse_url(url).host or "").strip("[]").lower()
    except Exception:
        return "the URL could not be parsed"
    try:
        expected = _wire_host(host)
    except (UnicodeError, ImportError):
        return "the URL's host is not a valid internationalized domain name"
    if sent_to != expected:
        return "the URL's host is ambiguous"
    # The lookup uses the name the connection will use. Python's own codec
    # would turn a Unicode name into a different one for some letters
    # (IDNA 2003 reads "straße" as "strasse").
    try:
        infos = socket.getaddrinfo(sent_to, None)
    except (socket.gaierror, UnicodeError) as e:
        return f"could not resolve {host}: {e}"
    for info in infos:
        if not is_public_address(info[4][0]):
            return f"{host} resolves to a private or local address ({info[4][0]})"
    return None


def _wire_host(host: str) -> str:
    """``host`` as requests puts it on the wire: lower-cased, each non-ASCII
    label IDNA-encoded ("münchen.de" -> "xn--mnchen-3ya.de") with the rules
    urllib3 applies, so the two can be compared. Raises UnicodeError for a
    label that has no such form."""
    host = host.lower()
    if host.isascii():
        return host
    import idna
    return ".".join(
        label if label.isascii() else idna.encode(label, strict=True, std3_rules=True).decode("ascii")
        for label in host.split(".")
    )


# IPv6 ranges whose last 32 bits are an IPv4 address: IPv4-compatible,
# IPv4-translated (SIIT, ::ffff:0:a.b.c.d) and NAT64.
_IPV4_IN_LOW_BITS = tuple(ipaddress.ip_network(net) for net in (
    "::/96", "::ffff:0:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
))


def is_public_address(address: str) -> bool:
    """True when ``address`` is globally routable, including the IPv4 address an
    IPv6 form carries (mapped, translated, IPv4-compatible, NAT64, 6to4, Teredo)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return False
    candidates = [ip]
    if ip.version == 6:
        embedded = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if any(ip in net for net in _IPV4_IN_LOW_BITS):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        candidates += [e for e in embedded if e is not None]
    return all(
        c.is_global and not c.is_multicast and not (c.version == 6 and c.is_site_local)
        for c in candidates
    )


@functools.lru_cache(maxsize=None)
def _no_netrc_session_class():
    import requests
    from requests.auth import AuthBase

    class _NoAuth(AuthBase):
        def __call__(self, r):
            return r

    class NoNetrcSession(requests.Session):
        def __init__(self):
            super().__init__()
            # A session-level auth stops prepare_request from looking up .netrc.
            self.auth = _NoAuth()

        def rebuild_auth(self, prepared_request, response):
            # requests' own version also reads .netrc for every redirect target,
            # whatever auth was set; keep only its removal of the Authorization
            # header when a redirect leaves the original host.
            headers = prepared_request.headers
            if "Authorization" in headers and self.should_strip_auth(
                response.request.url, prepared_request.url
            ):
                del headers["Authorization"]

    return NoNetrcSession


def no_netrc_session():
    """A requests Session that never sends logins from ~/.netrc (or $NETRC).

    requests reads .netrc on every request and every redirect hop, and a
    ``default`` entry there matches any host, so a fetch of a URL someone else
    chose would carry the user's saved login to it. Proxy settings and CA
    bundles from the environment still apply; an auth passed to a request
    explicitly is still sent.
    """
    return _no_netrc_session_class()()


class OpenConnections:
    """The sockets one session has connected.

    ``cut()`` shuts them down, which ends whatever the session is blocked on: a
    wait for response headers, or a read of a body that arrives a byte at a
    time. A socket timeout cannot do that, since it starts again with every
    byte received. After a cut the session opens no new connection.

    The sockets themselves are kept, not the connection objects: for a response
    that closes its connection, http.client drops the connection's reference to
    the socket while the body is still being read from it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sockets = []
        self.was_cut = False

    def add(self, sock) -> None:
        if sock is None:
            return
        with self._lock:
            self._sockets.append(sock)
            cut_now = self.was_cut
        if cut_now:
            self._shut_down(sock)

    def cut(self) -> None:
        with self._lock:
            self.was_cut = True
            sockets = list(self._sockets)
        for sock in sockets:
            self._shut_down(sock)

    @staticmethod
    def _shut_down(sock) -> None:
        try:
            # The plain socket call: ssl's own shutdown() also drops the TLS
            # state that a reader in another thread is still using.
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except (OSError, TypeError, ValueError):
            pass


def fetch_session(public_only: bool = False):
    """A requests Session for fetching a URL someone else chose.

    It never sends ~/.netrc logins (see :func:`no_netrc_session`), and with
    ``public_only`` it connects only to globally routable addresses (see
    :func:`public_only_session`). ``session.connections`` is an
    :class:`OpenConnections`; a caller that wants an overall time limit calls
    its ``cut()`` when the time is up. A connection through a SOCKS proxy from
    the environment (possible without ``public_only``) is made by urllib3's
    SOCKS classes and is not in it.

    With ``allow_redirects=False`` a redirect response comes back with its body
    unread, for the caller to close.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from urllib3.exceptions import ConnectTimeoutError, NameResolutionError
    from urllib3.poolmanager import ProxyManager
    from urllib3.util import connection as u3conn

    connections = OpenConnections()

    class _Tracked:
        def connect(self):
            if connections.was_cut:
                raise ConnectTimeoutError(self, "the time limit for this fetch has passed")
            super().connect()
            connections.add(self.sock)

    class _PublicOnly:
        def _new_conn(self):
            try:
                infos = socket.getaddrinfo(self._dns_host, self.port, type=socket.SOCK_STREAM)
            except socket.gaierror as e:
                raise NameResolutionError(self.host, self, e) from e
            for info in infos:
                if not is_public_address(info[4][0]):
                    raise PrivateAddressError(self, self._dns_host, info[4][0])
            error = None
            for info in infos:
                if connections.was_cut:
                    raise ConnectTimeoutError(self, "the time limit for this fetch has passed")
                try:
                    return u3conn.create_connection(
                        (info[4][0], self.port), self.timeout,
                        source_address=self.source_address, socket_options=self.socket_options)
                except OSError as e:
                    error = e
            raise NewConnectionError(self, f"Failed to establish a new connection: {error}")

    rules = (_Tracked, _PublicOnly) if public_only else (_Tracked,)

    class _HTTP(*rules, HTTPConnection):
        pass

    class _HTTPS(*rules, HTTPSConnection):
        pass

    class _HTTPPool(HTTPConnectionPool):
        ConnectionCls = _HTTP

    class _HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = _HTTPS

    pools = {"http": _HTTPPool, "https": _HTTPSPool}

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = pools

        def proxy_manager_for(self, proxy, **proxy_kwargs):
            if public_only:
                raise requests.exceptions.ProxyError(
                    "public-only fetches do not go through a proxy")
            manager = super().proxy_manager_for(proxy, **proxy_kwargs)
            # An HTTP(S) proxy uses the ordinary connection classes; a SOCKS
            # manager has its own, which must stay.
            if type(manager) is ProxyManager:
                manager.pool_classes_by_scheme = pools
            return manager

    class _FetchSession(_no_netrc_session_class()):
        def resolve_redirects(self, resp, req, *args, yield_requests=False, **kwargs):
            if yield_requests:
                # Session.send() asks for this when redirects are off, only to
                # fill Response.next, and requests reads the redirect's whole
                # body to build it.
                return iter(())
            return super().resolve_redirects(resp, req, *args, yield_requests=yield_requests, **kwargs)

    session = _FetchSession()
    session.connections = connections
    if public_only:
        # trust_env=False also turns off requests' own reading of REQUESTS_CA_BUNDLE
        # and CURL_CA_BUNDLE, so that part is restored here.
        session.trust_env = False
        session.verify = (
            os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or True
        )
    session.mount("http://", _Adapter())
    session.mount("https://", _Adapter())
    return session


def public_only_session():
    """A requests Session that connects only to globally routable addresses.

    The address is checked when the connection is made, and the socket goes to
    exactly the address that was checked, so a name that resolves differently
    between a check and the fetch (DNS rebinding) or a host that requests
    decodes differently from the checker (percent-encoding) cannot reach this
    machine or its networks. TLS is still verified against the host name.

    The session ignores the environment's HTTP(S)_PROXY / ALL_PROXY settings and
    ~/.netrc (or $NETRC): a proxy would open the connection itself, past the
    address check, and .netrc would hand the user's saved logins to whatever
    host is fetched (its ``default`` entry to every host). A proxy passed to a
    request explicitly is refused for the same reason. A CA bundle named in
    REQUESTS_CA_BUNDLE or CURL_CA_BUNDLE is still used.
    """
    return fetch_session(public_only=True)
