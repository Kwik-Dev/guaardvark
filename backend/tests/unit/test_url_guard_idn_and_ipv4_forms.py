"""The URL guard reads an internationalized host name the way the request is
sent (its "xn--" form), and judges an IPv6 address that carries an IPv4
address by that address. No name is looked up for real."""

import socket

import pytest

from backend.utils.hosts import is_public_address, private_address_reason

PUBLIC = "93.184.216.34"


@pytest.fixture
def lookups(monkeypatch):
    """Answers every lookup with a public address and records the names asked."""
    asked = []

    def fake(host, port, *args, **kwargs):
        asked.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return asked


@pytest.mark.parametrize("url,wire_host", [
    ("https://münchen.de/", "xn--mnchen-3ya.de"),
    ("https://MÜNCHEN.de/pfad", "xn--mnchen-3ya.de"),
    ("https://bücher.de/", "xn--bcher-kva.de"),
    ("https://例子.中国/", "xn--fsqu00a.xn--fiqs8s"),
    ("http://user@münchen.de:8080/p", "xn--mnchen-3ya.de"),
    ("https://xn--mnchen-3ya.de/", "xn--mnchen-3ya.de"),
    ("https://Example.COM/", "example.com"),
])
def test_an_internationalized_name_passes_and_is_looked_up_in_its_wire_form(lookups, url, wire_host):
    assert private_address_reason(url) is None
    assert lookups == [wire_host]


def test_the_lookup_is_for_the_name_requests_connects_to(lookups):
    """Python's idna codec reads "straße" as "strasse", another domain."""
    assert private_address_reason("https://straße.de/") is None
    assert lookups == ["xn--strae-oqa.de"]


def test_an_internationalized_name_on_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("10.1.2.3", 0))])
    assert "10.1.2.3" in private_address_reason("https://internü.example/")


@pytest.mark.parametrize("url", [
    "http://münchen.de\\@127.0.0.1/",
    "http://☃.net/",                     # not a valid IDNA label
    "http://ｅｘａｍｐｌｅ.com/",             # full-width letters
])
def test_names_the_two_parsers_cannot_agree_on_are_refused_without_a_lookup(lookups, url):
    assert private_address_reason(url)
    assert lookups == []


@pytest.mark.parametrize("address", [
    "::ffff:0:7f00:1",          # IPv4-translated (SIIT) 127.0.0.1
    "::ffff:0:a00:1",           # 10.0.0.1
    "::ffff:0:192.168.1.1",
    "::ffff:7f00:1",            # IPv4-mapped
    "::7f00:1",                 # IPv4-compatible
    "64:ff9b::7f00:1",          # NAT64
    "2002:7f00:1::1",           # 6to4
])
def test_an_ipv6_form_of_a_private_ipv4_address_is_not_public(address):
    assert not is_public_address(address)
    assert private_address_reason(f"http://[{address}]/")


@pytest.mark.parametrize("address", ["::ffff:0:808:808", "::ffff:8.8.8.8", "64:ff9b::808:808", "8.8.8.8"])
def test_the_same_forms_of_a_public_ipv4_address_stay_public(address):
    assert is_public_address(address)
