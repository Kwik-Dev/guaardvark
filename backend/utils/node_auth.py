"""Each machine's own key, and the signatures requests between machines carry.

Every Guaardvark holds an Ed25519 key pair in node_ed25519.pem, beside its
node_id file (backend/utils/node_identity.py), readable only by its owner. A
client joins the fleet once: it sends the fleet key and its public key to the
master, and the operator approves it there. The master then signs a
certificate for that machine, valid for CERT_LIFETIME_S and renewed through
the heartbeat:

    v1.<base64url JSON {"v", "node", "pub", "iat", "exp", "iss"}>.<base64url signature>

Every request between machines carries:

    X-GX-Node   the sender's node id
    X-GX-Cert   the sender's certificate (absent only on the enrolment request)
    X-GX-Ts     Unix seconds when it was signed
    X-GX-Nonce  random, used once
    X-GX-Sig    Ed25519 signature of canonical_request()

The receiver checks the certificate against the master's public key (its own
on the master, the one pinned at enrolment on a client), that the node id in
the header is the certificate's, that the time is within CLOCK_SKEW_S of its
own clock, the signature, and that the nonce has not been seen. The master
also checks that the machine is still approved with that key; another client
relies on the certificate's expiry. The node id in a URL must be the
certificate's; the routes check that themselves.

The master signs its own certificate, so its requests to clients (broadcasts,
directives) carry the same headers and are recognised by the pinned key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

HEADER_NODE = "X-GX-Node"
HEADER_CERT = "X-GX-Cert"
HEADER_TS = "X-GX-Ts"
HEADER_NONCE = "X-GX-Nonce"
HEADER_SIG = "X-GX-Sig"

CERT_VERSION = "v1"
CERT_LIFETIME_S = 24 * 3600
# The heartbeat hands out a fresh certificate once less than this is left.
CERT_RENEW_BEFORE_S = 12 * 3600
CLOCK_SKEW_S = 30
# A nonce is remembered for longer than any timestamp stays acceptable.
NONCE_TTL_S = 2 * CLOCK_SKEW_S + 30
NONCE_CACHE_LIMIT = 500_000

KEY_FILE = "node_ed25519.pem"
FLEET_STATE_FILE = "node_fleet.json"

# Socket.IO handshakes are signed as this method and path.
SOCKET_METHOD = "CONNECT"
SOCKET_PATH = "/socket.io/"

_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class NodeAuthError(Exception):
    """A request that does not prove which machine sent it.

    status is 401 when the proof is missing, malformed, expired, replayed or
    wrong, and 403 when the proof is good but names a machine that may not
    do this.
    """

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Certificate:
    node: str
    pub: str
    iat: int
    exp: int
    iss: str
    token: str


# ---- encoding ---------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _ed25519():
    # Imported on use: the module loads (and refuses every request) where the
    # cryptography package is missing.
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519


def public_key_text(private_key) -> str:
    """The raw 32-byte public key as base64url."""
    from cryptography.hazmat.primitives import serialization

    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return _b64e(raw)


def load_public_key(text: str):
    try:
        raw = _b64d(text)
    except (ValueError, TypeError) as exc:
        raise NodeAuthError(401, "The public key is not valid.") from exc
    if len(raw) != 32:
        raise NodeAuthError(401, "The public key is not valid.")
    return _ed25519().Ed25519PublicKey.from_public_bytes(raw)


def fingerprint(public_key: str) -> str:
    """A short form of a public key that a person can compare on two screens."""
    try:
        raw = _b64d(public_key)
    except (ValueError, TypeError):
        return ""
    digest = hashlib.sha256(raw).hexdigest()[:20]
    return " ".join(digest[i:i + 4] for i in range(0, 20, 4))


def body_sha256(body: Optional[bytes]) -> str:
    return hashlib.sha256(body or b"").hexdigest() if body else _EMPTY_SHA256


def canonical_request(method: str, path: str, ts: int, nonce: str, body_hash: str) -> bytes:
    """What a request signature covers. path includes the query string."""
    return f"GX-REQ-1\n{method.upper()}\n{path}\n{ts}\n{nonce}\n{body_hash}".encode("utf-8")


def path_with_query(path: str, query: str) -> str:
    return f"{path}?{query}" if query else path


# ---- certificates -------------------------------------------------------------

def issue_certificate(signing_key, node_id: str, node_public_key: str, issuer: str,
                      now: Optional[int] = None, lifetime_s: int = CERT_LIFETIME_S) -> str:
    """The master's signed statement that node_id holds node_public_key."""
    issued = int(time.time() if now is None else now)
    payload = {
        "v": 1,
        "node": node_id,
        "pub": node_public_key,
        "iat": issued,
        "exp": issued + int(lifetime_s),
        "iss": issuer,
    }
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signed = f"{CERT_VERSION}.{body}".encode("ascii")
    return f"{CERT_VERSION}.{body}.{_b64e(signing_key.sign(signed))}"


def read_certificate(token: str, authority_public_key: str, now: Optional[int] = None) -> Certificate:
    """A certificate signed by the authority and not yet expired, or NodeAuthError."""
    if not token or not authority_public_key:
        raise NodeAuthError(401, "No certificate, or no master key to check it with.")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != CERT_VERSION:
        raise NodeAuthError(401, "The certificate is malformed.")
    try:
        signature = _b64d(parts[2])
        payload = json.loads(_b64d(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NodeAuthError(401, "The certificate is malformed.") from exc
    authority = load_public_key(authority_public_key)
    try:
        authority.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except Exception as exc:
        raise NodeAuthError(401, "The certificate was not signed by this fleet's master.") from exc
    if not isinstance(payload, dict):
        raise NodeAuthError(401, "The certificate is malformed.")
    node, pub, iss = payload.get("node"), payload.get("pub"), payload.get("iss")
    iat, exp = payload.get("iat"), payload.get("exp")
    if not (isinstance(node, str) and node and isinstance(pub, str) and isinstance(iss, str)
            and isinstance(iat, int) and isinstance(exp, int)):
        raise NodeAuthError(401, "The certificate is malformed.")
    current = int(time.time() if now is None else now)
    if exp <= current:
        raise NodeAuthError(401, "The certificate has expired; the machine must enrol again.")
    return Certificate(node=node, pub=pub, iat=iat, exp=exp, iss=iss, token=token)


# ---- replay protection --------------------------------------------------------

class NonceCache:
    """Nonces seen in the last NONCE_TTL_S seconds, per sending machine."""

    def __init__(self, ttl_s: int = NONCE_TTL_S, limit: int = NONCE_CACHE_LIMIT):
        self._ttl = ttl_s
        self._limit = limit
        self._seen: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()
        self._next_purge = 0.0

    def claim(self, node_id: str, nonce: str, now: Optional[float] = None) -> bool:
        """True the first time this machine uses this nonce; False after."""
        current = time.time() if now is None else now
        key = (node_id, nonce)
        with self._lock:
            if current >= self._next_purge or len(self._seen) >= self._limit:
                self._purge(current)
            expiry = self._seen.get(key)
            if expiry is not None and expiry > current:
                return False
            if len(self._seen) >= self._limit:
                # Full of live nonces: refuse rather than forget one.
                logger.warning("[NODE_AUTH] nonce cache full; refusing a signed request")
                return False
            self._seen[key] = current + self._ttl
            return True

    def _purge(self, current: float) -> None:
        self._seen = {k: v for k, v in self._seen.items() if v > current}
        self._next_purge = current + 5

    def __len__(self) -> int:
        return len(self._seen)


_nonce_cache = NonceCache()


def default_nonce_cache() -> NonceCache:
    return _nonce_cache


# ---- signing ------------------------------------------------------------------

def sign_request(private_key, node_id: str, certificate: Optional[str], method: str,
                 path: str, body: Optional[bytes] = None, now: Optional[int] = None,
                 nonce: Optional[str] = None) -> dict:
    """Headers that prove this machine sent this request. path includes the
    query string. Without a certificate (the enrolment request) the receiver
    checks the signature against the public key in the body."""
    ts = int(time.time() if now is None else now)
    nonce = nonce or secrets.token_urlsafe(18)
    signature = private_key.sign(canonical_request(method, path, ts, nonce, body_sha256(body)))
    headers = {
        HEADER_NODE: node_id,
        HEADER_TS: str(ts),
        HEADER_NONCE: nonce,
        HEADER_SIG: _b64e(signature),
    }
    if certificate:
        headers[HEADER_CERT] = certificate
    return headers


def _header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return (value or "").strip()


def is_signed(headers: Mapping[str, str]) -> bool:
    return bool(_header(headers, HEADER_SIG))


def verify_signature(method: str, path: str, headers: Mapping[str, str], body: Optional[bytes],
                     public_key: str, nonce_cache: Optional[NonceCache] = None,
                     now: Optional[int] = None) -> str:
    """Check the request was signed by public_key, recently and once.
    Returns the node id the request names."""
    node_id = _header(headers, HEADER_NODE)
    ts_text = _header(headers, HEADER_TS)
    nonce = _header(headers, HEADER_NONCE)
    signature_text = _header(headers, HEADER_SIG)
    if not (node_id and ts_text and nonce and signature_text):
        raise NodeAuthError(401, "This request is not signed by an enrolled machine.")
    try:
        ts = int(ts_text)
    except ValueError as exc:
        raise NodeAuthError(401, "The request time is not valid.") from exc
    current = int(time.time() if now is None else now)
    if abs(current - ts) > CLOCK_SKEW_S:
        raise NodeAuthError(
            401, f"The request was signed more than {CLOCK_SKEW_S} seconds from this machine's "
                 "clock; check both clocks.")
    if not _NONCE_RE.match(nonce):
        raise NodeAuthError(401, "The request nonce is not valid.")
    key = load_public_key(public_key)
    try:
        key.verify(_b64d(signature_text), canonical_request(method, path, ts, nonce, body_sha256(body)))
    except Exception as exc:
        raise NodeAuthError(401, "The request signature does not match.") from exc
    cache = nonce_cache if nonce_cache is not None else _nonce_cache
    if not cache.claim(node_id, nonce, current):
        raise NodeAuthError(401, "This request was already received once.")
    return node_id


def verify_request(method: str, path: str, headers: Mapping[str, str], body: Optional[bytes],
                   authority_public_key: Optional[str], nonce_cache: Optional[NonceCache] = None,
                   now: Optional[int] = None) -> Certificate:
    """A request signed by a machine holding a certificate from the authority."""
    if not is_signed(headers):
        raise NodeAuthError(401, "This request is not signed by an enrolled machine.")
    cert = read_certificate(_header(headers, HEADER_CERT), authority_public_key or "", now)
    if _header(headers, HEADER_NODE) != cert.node:
        raise NodeAuthError(401, "The certificate belongs to another machine.")
    verify_signature(method, path, headers, body, cert.pub, nonce_cache, now)
    return cert


class NodeSignature:
    """requests auth that signs the request as sent (final URL and body)."""

    def __init__(self, private_key, node_id: str, certificate: Optional[str] = None):
        self._key = private_key
        self._node_id = node_id
        self._certificate = certificate

    def __call__(self, prepared):
        parts = urlsplit(prepared.url)
        body = prepared.body
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif body is not None and not isinstance(body, bytes):
            raise NodeAuthError(401, "A streamed body cannot be signed.")
        prepared.headers.update(sign_request(
            self._key, self._node_id, self._certificate, prepared.method,
            path_with_query(unquote(parts.path), parts.query), body,
        ))
        return prepared


# ---- this machine's key and fleet state ---------------------------------------

_key_lock = threading.Lock()
_keys: dict[str, object] = {}
_state_lock = threading.Lock()


def identity_dir() -> Path:
    from backend.utils import node_identity

    return Path(node_identity.DEFAULT_NODE_ID_PATH).parent


def load_or_create_private_key(directory: Optional[Path] = None, create: bool = True):
    """This machine's private key, created on first use (owner read/write
    only). With create=False, None when there is none yet."""
    from cryptography.hazmat.primitives import serialization

    path = Path(directory or identity_dir()) / KEY_FILE
    with _key_lock:
        cached = _keys.get(str(path))
        if cached is not None:
            return cached
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except FileNotFoundError:
            if not create:
                return None
            key = _ed25519().Ed25519PrivateKey.generate()
            pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written whole, then linked into place: another process starting
            # at the same moment sees no file or a complete one, and keeps
            # whichever key got there first.
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(pem)
            try:
                os.link(tmp, path)
            except FileExistsError:
                key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            except OSError:
                # A filesystem without hard links: create it exclusively instead.
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
                else:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(pem)
            finally:
                os.unlink(tmp)
        if not isinstance(key, _ed25519().Ed25519PrivateKey):
            raise NodeAuthError(401, f"{path.name} does not hold an Ed25519 key.")
        _keys[str(path)] = key
        return key


def local_public_key() -> str:
    return public_key_text(load_or_create_private_key())


def local_fingerprint() -> Optional[str]:
    """This machine's key fingerprint, or None before it has a key. Never
    creates one, so a read leaves nothing behind."""
    key = load_or_create_private_key(create=False)
    return fingerprint(public_key_text(key)) if key is not None else None


def read_fleet_state(directory: Optional[Path] = None) -> dict:
    """What this client knows about its fleet: its certificate, the master's
    pinned key and id, and the master URL they belong to."""
    path = Path(directory or identity_dir()) / FLEET_STATE_FILE
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_fleet_state(state: dict, directory: Optional[Path] = None) -> None:
    path = Path(directory or identity_dir()) / FLEET_STATE_FILE
    with _state_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
        os.replace(tmp, path)


def certificate_usable(state: dict, now: Optional[int] = None) -> bool:
    current = int(time.time() if now is None else now)
    return bool(state.get("certificate")) and int(state.get("certificate_expires") or 0) > current + 60


# ---- the fleet's authority ----------------------------------------------------

def _interconnector_config() -> dict:
    try:
        from backend.api.interconnector_api import _get_config

        return _get_config() or {}
    except Exception as exc:
        logger.debug("[NODE_AUTH] interconnector config unavailable: %s", exc)
        return {}


def is_master() -> bool:
    return _interconnector_config().get("node_mode") == "master"


def authority() -> tuple[Optional[str], Optional[str]]:
    """(public key, node id) of the fleet's master: this machine in master
    mode, otherwise the master pinned at enrolment."""
    if is_master():
        from backend.utils.node_identity import local_node_id

        return local_public_key(), local_node_id()
    state = read_fleet_state()
    return state.get("master_public_key"), state.get("master_node_id")


_self_cert_lock = threading.Lock()
_self_cert: dict = {}


def local_credentials() -> Optional[tuple[object, str, str]]:
    """(private key, node id, certificate) this machine signs with, or None
    while it holds no usable certificate."""
    from backend.utils.node_identity import local_node_id

    if is_master():
        key = load_or_create_private_key()
        node_id = local_node_id()
        now = int(time.time())
        with _self_cert_lock:
            if (_self_cert.get("node") != node_id
                    or _self_cert.get("exp", 0) - now < CERT_RENEW_BEFORE_S):
                pub = public_key_text(key)
                token = issue_certificate(key, node_id, pub, issuer=node_id, now=now)
                _self_cert.update({"node": node_id, "exp": now + CERT_LIFETIME_S, "token": token})
            return key, node_id, _self_cert["token"]
    state = read_fleet_state()
    if not certificate_usable(state):
        return None
    # A client signs only with the key it enrolled with; it never makes one here.
    key = load_or_create_private_key(create=False)
    if key is None:
        return None
    return key, state.get("node_id") or local_node_id(), state["certificate"]


def request_auth() -> Optional[NodeSignature]:
    """requests auth for this machine, or None while it holds no certificate."""
    try:
        creds = local_credentials()
    except Exception as exc:
        logger.warning("[NODE_AUTH] cannot sign as this machine: %s", exc)
        return None
    if creds is None:
        return None
    key, node_id, cert = creds
    return NodeSignature(key, node_id, cert)


def socket_auth() -> dict:
    """The auth payload for a Socket.IO connection to another machine."""
    creds = local_credentials()
    if creds is None:
        return {}
    key, node_id, cert = creds
    headers = sign_request(key, node_id, cert, SOCKET_METHOD, SOCKET_PATH)
    return {
        "gx_node": headers[HEADER_NODE],
        "gx_cert": headers[HEADER_CERT],
        "gx_ts": headers[HEADER_TS],
        "gx_nonce": headers[HEADER_NONCE],
        "gx_sig": headers[HEADER_SIG],
    }


def verify_socket_auth(auth: Mapping) -> Certificate:
    headers = {
        HEADER_NODE: str(auth.get("gx_node") or ""),
        HEADER_CERT: str(auth.get("gx_cert") or ""),
        HEADER_TS: str(auth.get("gx_ts") or ""),
        HEADER_NONCE: str(auth.get("gx_nonce") or ""),
        HEADER_SIG: str(auth.get("gx_sig") or ""),
    }
    authority_key, _ = authority()
    return verify_request(SOCKET_METHOD, SOCKET_PATH, headers, b"", authority_key)


def is_master_certificate(cert: Certificate) -> bool:
    """True when the certificate is the master's own: its node id and key are
    the ones pinned (or this machine's, on the master)."""
    authority_key, authority_node = authority()
    return bool(authority_key) and cert.pub == authority_key and cert.node == authority_node


# ---- Flask --------------------------------------------------------------------

def verify_flask_request(nonce_cache: Optional[NonceCache] = None) -> Certificate:
    """The current Flask request, signed by an enrolled machine of this fleet."""
    from flask import request

    authority_key, _ = authority()
    return verify_request(
        request.method,
        path_with_query(request.path, request.query_string.decode("latin-1")),
        request.headers,
        request.get_data(cache=True),
        authority_key,
        nonce_cache,
    )


def verify_flask_request_from_master() -> Certificate:
    """The current Flask request, signed by the fleet's master itself."""
    cert = verify_flask_request()
    if not is_master_certificate(cert):
        raise NodeAuthError(403, "Only the fleet's master may send this.")
    return cert


def flask_request_signed_by_master() -> bool:
    from flask import request

    if not is_signed(request.headers):
        return False
    try:
        verify_flask_request_from_master()
        return True
    except NodeAuthError as exc:
        logger.warning("[NODE_AUTH] refused %s: %s", request.path, exc.reason)
        return False
    except Exception as exc:
        logger.warning("[NODE_AUTH] could not check %s: %s", request.path, exc)
        return False
