"""Signing and checking requests between machines (backend/utils/node_auth.py).

Pure functions: keys, the master's certificates, request signatures, the clock
window and the replay cache. No Flask app, database or network.
"""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.utils import node_auth
from backend.utils.node_auth import NodeAuthError

PATH = "/api/interconnector/nodes/node-a/heartbeat"


@pytest.fixture
def master():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def node_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def cert(master, node_key):
    return node_auth.issue_certificate(
        master, "node-a", node_auth.public_key_text(node_key), issuer="master-1")


def _pub(key):
    return node_auth.public_key_text(key)


def _verify(headers, master, *, method="POST", path=PATH, body=b"{}", cache=None, now=None):
    return node_auth.verify_request(method, path, headers, body, _pub(master),
                                    cache if cache is not None else node_auth.NonceCache(), now)


# ---- keys and certificates ----------------------------------------------------

def test_public_key_text_is_32_raw_bytes(node_key):
    text = _pub(node_key)
    assert len(node_auth._b64d(text)) == 32
    assert node_auth.load_public_key(text) is not None


@pytest.mark.parametrize("bad", ["", "not base64!", node_auth._b64e(b"short")])
def test_a_malformed_public_key_is_refused(bad):
    with pytest.raises(NodeAuthError) as exc:
        node_auth.load_public_key(bad)
    assert exc.value.status == 401


def test_fingerprints_differ_per_key_and_are_stable(node_key, master):
    assert node_auth.fingerprint(_pub(node_key)) == node_auth.fingerprint(_pub(node_key))
    assert node_auth.fingerprint(_pub(node_key)) != node_auth.fingerprint(_pub(master))
    assert len(node_auth.fingerprint(_pub(node_key)).split()) == 5


def test_the_master_signs_what_it_states(cert, master, node_key):
    read = node_auth.read_certificate(cert, _pub(master))
    assert (read.node, read.pub, read.iss) == ("node-a", _pub(node_key), "master-1")
    assert read.exp - read.iat == node_auth.CERT_LIFETIME_S


def test_a_certificate_from_another_master_is_refused(cert):
    with pytest.raises(NodeAuthError) as exc:
        node_auth.read_certificate(cert, _pub(Ed25519PrivateKey.generate()))
    assert exc.value.status == 401


def test_an_expired_certificate_is_refused(master, node_key):
    old = node_auth.issue_certificate(master, "node-a", _pub(node_key), "m",
                                      now=int(time.time()) - 2 * node_auth.CERT_LIFETIME_S)
    with pytest.raises(NodeAuthError) as exc:
        node_auth.read_certificate(old, _pub(master))
    assert "expired" in exc.value.reason


def test_an_edited_certificate_is_refused(cert, master):
    version, payload, signature = cert.split(".")
    claims = json.loads(node_auth._b64d(payload))
    claims["node"] = "node-b"
    forged = ".".join([version, node_auth._b64e(json.dumps(claims).encode()), signature])
    with pytest.raises(NodeAuthError):
        node_auth.read_certificate(forged, _pub(master))


@pytest.mark.parametrize("token", ["", "v1.abc", "v2.a.b", "v1.!!.??", "v1.e30.AAAA"])
def test_a_malformed_certificate_is_refused(token, master):
    with pytest.raises(NodeAuthError) as exc:
        node_auth.read_certificate(token, _pub(master))
    assert exc.value.status == 401


# ---- request signatures ---------------------------------------------------------

def test_a_signed_request_names_its_certificate(node_key, cert, master):
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    read = _verify(headers, master)
    assert read.node == "node-a"


def test_an_unsigned_request_is_refused(master):
    with pytest.raises(NodeAuthError) as exc:
        _verify({}, master)
    assert exc.value.status == 401


@pytest.mark.parametrize("change", [
    {"method": "PUT"},
    {"path": "/api/interconnector/nodes/node-b/heartbeat"},
    {"path": PATH + "?extra=1"},
    {"body": b'{"x": 1}'},
])
def test_any_change_to_the_request_breaks_the_signature(node_key, cert, master, change):
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    with pytest.raises(NodeAuthError) as exc:
        _verify(headers, master, **change)
    assert "signature" in exc.value.reason


def test_the_query_string_is_signed(node_key, cert, master):
    headers = node_auth.sign_request(node_key, "node-a", cert, "GET", PATH + "?a=1&a=2", b"")
    assert _verify(headers, master, method="GET", path=PATH + "?a=1&a=2", body=b"").node == "node-a"


def test_a_request_signed_by_another_key_is_refused(cert, master):
    headers = node_auth.sign_request(Ed25519PrivateKey.generate(), "node-a", cert, "POST", PATH, b"{}")
    with pytest.raises(NodeAuthError):
        _verify(headers, master)


def test_a_header_naming_another_machine_is_refused(node_key, cert, master):
    headers = node_auth.sign_request(node_key, "node-b", cert, "POST", PATH, b"{}")
    with pytest.raises(NodeAuthError) as exc:
        _verify(headers, master)
    assert "another machine" in exc.value.reason


def test_a_replayed_request_is_refused(node_key, cert, master):
    cache = node_auth.NonceCache()
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    _verify(headers, master, cache=cache)
    with pytest.raises(NodeAuthError) as exc:
        _verify(headers, master, cache=cache)
    assert "already received" in exc.value.reason


@pytest.mark.parametrize("offset", [-node_auth.CLOCK_SKEW_S - 1, node_auth.CLOCK_SKEW_S + 1])
def test_a_request_outside_the_clock_window_is_refused(node_key, cert, master, offset):
    now = int(time.time())
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}", now=now + offset)
    with pytest.raises(NodeAuthError) as exc:
        _verify(headers, master, now=now)
    assert "clock" in exc.value.reason


@pytest.mark.parametrize("offset", [-node_auth.CLOCK_SKEW_S, node_auth.CLOCK_SKEW_S])
def test_a_request_at_the_edge_of_the_window_is_accepted(node_key, cert, master, offset):
    now = int(time.time())
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}", now=now + offset)
    assert _verify(headers, master, now=now).node == "node-a"


@pytest.mark.parametrize("nonce", ["short", "x" * 65, "has space in it here", "semi;colon;semi;colon"])
def test_a_malformed_nonce_is_refused(node_key, cert, master, nonce):
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}", nonce=nonce)
    with pytest.raises(NodeAuthError):
        _verify(headers, master)


def test_the_enrolment_proof_is_checked_against_the_key_in_the_body(node_key):
    body = b'{"public_key": "..."}'
    headers = node_auth.sign_request(node_key, "node-a", None, "POST", "/r", body)
    assert node_auth.HEADER_CERT not in headers
    assert node_auth.verify_signature("POST", "/r", headers, body, _pub(node_key),
                                      node_auth.NonceCache()) == "node-a"
    with pytest.raises(NodeAuthError):
        node_auth.verify_signature("POST", "/r", headers, body,
                                   _pub(Ed25519PrivateKey.generate()), node_auth.NonceCache())


def test_header_names_are_case_insensitive_mappings(node_key, cert, master):
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    lowered = {k.lower(): v for k, v in headers.items()}
    assert _verify(lowered, master).node == "node-a"


# ---- replay cache -------------------------------------------------------------

def test_the_nonce_cache_forgets_after_its_ttl():
    cache = node_auth.NonceCache(ttl_s=10)
    assert cache.claim("a", "n" * 16, now=100.0) is True
    assert cache.claim("a", "n" * 16, now=105.0) is False
    assert cache.claim("b", "n" * 16, now=105.0) is True  # per machine
    assert cache.claim("a", "n" * 16, now=111.0) is True


def test_a_full_nonce_cache_refuses_rather_than_forgets():
    cache = node_auth.NonceCache(ttl_s=100, limit=2)
    assert cache.claim("a", "1" * 16, now=0.0)
    assert cache.claim("a", "2" * 16, now=0.0)
    assert cache.claim("a", "3" * 16, now=1.0) is False
    assert cache.claim("a", "3" * 16, now=200.0) is True  # expired entries purged


def test_the_nonce_outlives_the_clock_window():
    assert node_auth.NONCE_TTL_S > 2 * node_auth.CLOCK_SKEW_S


def test_an_empty_nonce_cache_is_still_used(node_key, cert, master):
    """An empty cache is truthy, so `cache or default` keeps the one given and
    the nonce lands in it (an empty cache was falsy through __len__ once,
    and a replay went through)."""
    cache = node_auth.NonceCache()
    assert bool(cache) is True and len(cache) == 0
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    _verify(headers, master, cache=cache)
    assert len(cache) == 1


def test_a_replay_is_refused_through_the_default_cache(node_key, cert, master):
    headers = node_auth.sign_request(node_key, "node-a", cert, "POST", PATH, b"{}")
    node_auth.verify_request("POST", PATH, headers, b"{}", _pub(master))
    with pytest.raises(NodeAuthError):
        node_auth.verify_request("POST", PATH, headers, b"{}", _pub(master))


def test_the_nonce_cache_never_grows_past_its_limit():
    cache = node_auth.NonceCache(ttl_s=10, limit=5)
    accepted = 0
    for second in range(40):
        for n in range(3):
            accepted += cache.claim("a", f"{second:04d}-{n}".ljust(16, "x"), now=float(second))
            assert len(cache) <= 5
    # Refused while full of live nonces, accepted again as they expire.
    assert 0 < accepted < 120


def test_enrolment_proofs_are_remembered_apart():
    assert node_auth.enrolment_nonce_cache() is not node_auth.default_nonce_cache()
    assert node_auth.enrolment_nonce_cache()._limit < node_auth.NONCE_CACHE_LIMIT


# ---- requests auth ------------------------------------------------------------

def test_requests_auth_signs_the_url_and_body_as_sent(node_key, cert, master):
    import requests

    prepared = requests.Request(
        "GET", "http://master:5000/api/interconnector/sync/files/pull",
        params=[("paths", "backend/api/"), ("paths", "scripts/")],
        auth=node_auth.NodeSignature(node_key, "node-a", cert),
    ).prepare()
    path = "/api/interconnector/sync/files/pull?paths=backend%2Fapi%2F&paths=scripts%2F"
    assert _verify(prepared.headers, master, method="GET", path=path, body=b"").node == "node-a"

    posted = requests.Request(
        "POST", "http://master:5000" + PATH, json={"x": 1},
        auth=node_auth.NodeSignature(node_key, "node-a", cert),
    ).prepare()
    assert _verify(posted.headers, master, body=posted.body).node == "node-a"


# ---- this machine's key file --------------------------------------------------

def test_the_private_key_is_created_once_and_private(tmp_path):
    first = node_auth.load_or_create_private_key(tmp_path)
    path = tmp_path / node_auth.KEY_FILE
    assert path.stat().st_mode & 0o777 == 0o600
    node_auth._keys.clear()
    again = node_auth.load_or_create_private_key(tmp_path)
    assert _pub(first) == _pub(again)


def test_the_fleet_state_file_is_private(tmp_path):
    node_auth.write_fleet_state({"node_id": "n"}, tmp_path)
    assert node_auth.read_fleet_state(tmp_path) == {"node_id": "n"}
    assert (tmp_path / node_auth.FLEET_STATE_FILE).stat().st_mode & 0o777 == 0o600


def test_a_certificate_close_to_expiry_is_not_used(cert):
    now = int(time.time())
    assert node_auth.certificate_usable({"certificate": cert, "certificate_expires": now + 3600}, now)
    assert not node_auth.certificate_usable({"certificate": cert, "certificate_expires": now + 30}, now)
    assert not node_auth.certificate_usable({}, now)
