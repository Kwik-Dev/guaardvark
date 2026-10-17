"""The Interconnector's fleet key: how it is checked and who may mint it.

The master stores only a SHA-256 hash of the key its clients send. The check
compares hashes in constant time.
"""
import hashlib

import pytest

from backend.api import interconnector_api as ic_api


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def test_verify_accepts_the_key_whose_hash_is_stored():
    assert ic_api._verify_api_key("fleet-key", _hash("fleet-key")) is True


@pytest.mark.parametrize("provided", ["fleet-kez", "", None, "fleet-key ", "ключ"])
def test_verify_refuses_any_other_key(provided):
    assert ic_api._verify_api_key(provided, _hash("fleet-key")) is False


@pytest.mark.parametrize("stored", ["", None])
def test_verify_refuses_when_no_hash_is_stored(stored):
    assert ic_api._verify_api_key("fleet-key", stored) is False


def test_verify_compares_in_constant_time(monkeypatch):
    seen = []
    real = ic_api.hmac.compare_digest

    def recording(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ic_api.hmac, "compare_digest", recording)
    assert ic_api._verify_api_key("fleet-key", _hash("fleet-key")) is True
    assert seen == [(_hash("fleet-key").encode(), _hash("fleet-key").encode())]
