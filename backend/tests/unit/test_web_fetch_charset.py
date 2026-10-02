"""A fetched page is decoded with the charset it declares: a byte-order mark,
then the Content-Type header, then the page's own <meta>, then UTF-8, and only
then a guess."""

import pytest
import requests
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.utils.web_fetch import content_type, decode_page

CAFE = "<html><head><title>Café crème</title></head><body><main>naïve résumé à la carte</main></body></html>"
CAFE_META = CAFE.replace("<head>", '<head><meta charset="windows-1252">')


def test_the_header_charset_is_used():
    text, encoding = decode_page(CAFE.encode("cp1252"), "windows-1252")
    assert "Café crème" in text and "naïve résumé à la carte" in text
    assert encoding == "cp1252"


def test_the_meta_charset_is_used_when_the_header_names_none():
    text, encoding = decode_page(CAFE_META.encode("cp1252"), None)
    assert "Café crème" in text and encoding == "cp1252"


def test_an_http_equiv_meta_counts_too():
    page = ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=iso-8859-2">'
            "<title>Žluťoučký kůň</title></head><body>úpěl ďábelské ódy</body></html>")
    text, encoding = decode_page(page.encode("iso-8859-2"), None)
    assert "Žluťoučký kůň" in text and encoding == "iso8859-2"


def test_utf8_needs_no_declaration():
    page = "<html><title>Überschrift – 東京</title><body>naïve café</body></html>"
    text, encoding = decode_page(page.encode("utf-8"), None)
    assert "Überschrift – 東京" in text and encoding == "utf-8"


def test_a_wrong_header_falls_through_to_the_meta_charset():
    text, encoding = decode_page(CAFE_META.encode("cp1252"), "utf-8")
    assert "Café crème" in text and encoding == "cp1252"


def test_a_byte_order_mark_wins_over_the_header():
    page = "<html><title>Überschrift</title></html>"
    text, encoding = decode_page(b"\xef\xbb\xbf" + page.encode("utf-8"), "windows-1252")
    assert text == page and encoding == "utf-8-sig"


def test_a_latin1_label_is_read_as_windows_1252():
    """Browsers do the same; the bytes 0x80-0x9F are quotes, dashes and the euro sign."""
    page = "<html><body>“Quoted” – €5</body></html>"
    text, encoding = decode_page(page.encode("cp1252"), "ISO-8859-1")
    assert "“Quoted” – €5" in text and encoding == "cp1252"


def test_a_multibyte_legacy_charset_from_the_header():
    page = "<html><title>日本語のページ</title><body>これはテストです。</body></html>"
    text, encoding = decode_page(page.encode("shift_jis"), "Shift_JIS")
    assert "日本語のページ" in text and encoding == "shift_jis"


def test_a_meta_that_claims_utf16_is_not_believed():
    page = '<html><head><meta charset="utf-16"><title>Plain</title></head><body>text</body></html>'
    text, encoding = decode_page(page.encode("utf-8"), None)
    assert "Plain" in text and encoding == "utf-8"


@pytest.mark.parametrize("label", ["hex", "zlib", "idna", "unicode-escape", "no-such-charset", "a\x00b"])
def test_a_label_that_is_not_a_page_encoding_is_ignored(label):
    page = "<html><title>Überschrift</title></html>"
    text, encoding = decode_page(page.encode("utf-8"), label)
    assert text == page and encoding == "utf-8"


def test_a_page_cut_inside_a_character_still_decodes():
    page = ("<html><body>" + "東京都の天気は晴れです。" * 50).encode("utf-8")
    cut = page[:-1]                                   # ends inside the last character
    text, encoding = decode_page(cut, "utf-8", complete=False)
    assert encoding == "utf-8" and "�" not in text
    assert text == page.decode("utf-8")[:-1]


def test_bytes_no_declared_charset_can_read_fall_back_without_failing():
    text, encoding = decode_page(b"<html><body>caf\xe9 \x81\x8d\x8f</body></html>", "utf-8")
    assert "caf" in text and encoding


@pytest.mark.parametrize("header,expected", [
    ("text/html; charset=windows-1252", ("text/html", "windows-1252")),
    ('TEXT/HTML; Charset="UTF-8"', ("text/html", "UTF-8")),
    ("text/html", ("text/html", None)),
    ("application/pdf", ("application/pdf", None)),
    ("text/html; boundary=x; charset=koi8-r", ("text/html", "koi8-r")),
    ("", ("", None)),
    (None, ("", None)),
])
def test_reading_a_content_type_header(header, expected):
    assert content_type(header) == expected


def test_a_fetch_honours_the_header_charset(monkeypatch):
    """End to end through extract_website_content; the transport is faked."""

    def fake_send(self, request, **kwargs):
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response.headers["Content-Type"] = "text/html; charset=windows-1252"
        response._content = CAFE.encode("cp1252")
        response._content_consumed = True
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    result = web_search_api.extract_website_content("https://legacy.example/menu")
    assert result["success"] and result["title"] == "Café crème"
    assert result["content"] == "naïve résumé à la carte"
