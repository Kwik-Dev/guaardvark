"""fetch_url and analyze_website fetch only globally routable addresses."""

import pytest

from backend.utils.hosts import private_address_reason


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5000/api/health",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data",
    "http://100.64.0.1/",              # CGNAT, used by Tailscale
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[fe80::1]/",
    "http://[fec0::1]/",               # site-local
    "http://224.0.0.1/",               # multicast
    "http://0.0.0.0/",
    "http://127.0.0.1:5002\\@example.com/",  # urllib3 would connect to 127.0.0.1
    "file:///etc/passwd",
    "ftp://example.com/",
    "http:///nohost",
])
def test_local_private_and_ambiguous_urls_are_refused(url):
    assert private_address_reason(url)


@pytest.mark.parametrize("url", ["http://8.8.8.8/", "https://[2606:4700:4700::1111]/"])
def test_public_addresses_pass(url):
    assert private_address_reason(url) is None


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(None, None, None, None, ("10.1.2.3", 0))])
    assert "10.1.2.3" in private_address_reason("https://intranet.example/")
