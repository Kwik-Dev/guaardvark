"""Fetch one web page, bounded in size and time, and decode it to text.

Used by extract_website_content (fetch_url, analyze_website, a URL in a
web_search query, the CSV generators), read_sitemap, and scrape_website when a
research task reads a page.
"""

from __future__ import annotations

import codecs
import threading
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

# ---------------------------------------------------------------------------
# The limits on one page fetch. They are declared here only; the tool
# descriptions in backend/tools/web_tools.py are built from these names.
# ---------------------------------------------------------------------------

# Redirects followed when only public addresses may be fetched. Without that
# rule the limit is requests' own (Session.max_redirects).
MAX_REDIRECTS = 5

# Longest silence accepted from a server: while connecting, while waiting for
# the response to start, and between two pieces of the body.
IDLE_SECONDS = 15

# Longest a whole fetch may take, redirects included. The idle limit alone does
# not bound a fetch: a server that sends a byte every few seconds never trips
# it. 45 s is three full idle waits, and well inside the 120 s an MCP call is
# given by default (backend/mcp/config.py, timeout_seconds), after which the
# client stops waiting but the fetch would otherwise go on.
DEADLINE_SECONDS = 45

# Most of a page body that is read. The callers keep a 2,000-character extract
# and word counts, and the whole body is held in memory and parsed. Measured
# with BeautifulSoup's html.parser on tag-dense HTML (Python 3.12, bs4 4.15):
# 2 MB parses in 0.7 s, adds 74 MB of peak memory and holds about 224,000 words
# of text; 5 MB takes 1.7 s and 184 MB; 20 MB takes 7.2 s and 734 MB. 2 MB
# keeps a fetch under a second of parsing and under 100 MB.
MAX_PAGE_BYTES = 2 * 1024 * 1024

_CHUNK_BYTES = 64 * 1024

# Response types that are read as a page. Anything else (a PDF, an image, an
# archive) is refused before its body is downloaded.
_PAGE_TYPE_PREFIXES = ("text/",)
_PAGE_TYPES = {"application/xhtml+xml", "application/xml", "application/json"}
_PAGE_TYPE_SUFFIXES = ("+xml", "+json")

# Where a file on this machine can be read instead, for a refusal that is about
# a local address or a non-web URL (file://).
LOCAL_FILE_ADVICE = "To read a file from this machine, upload it and use process_file."

# Ends every refusal of a public-only fetch, so the person or the model reading
# it knows no web tool will reach the address and does not retry with another.
PUBLIC_ONLY_NOTE = (
    "Web fetches reach public internet addresses only, never this machine or its "
    "local network. " + LOCAL_FILE_ADVICE
)


class FetchRefused(Exception):
    """The URL, or a redirect from it, is not one that may be fetched."""


class FetchFailed(Exception):
    """The fetch ended without a page; the message says why."""


@dataclass
class Page:
    url: str                   # after redirects
    body: bytes
    media_type: str            # lower-cased, "" when the server sent none
    charset: Optional[str]     # from the Content-Type header
    cut: Optional[str] = None  # why ``body`` is not the whole page, in words


def megabytes(size: int) -> str:
    """``size`` in MB the way the messages and tool descriptions print it."""
    return f"{size / (1024 * 1024):g} MB"


def _timed_out() -> FetchFailed:
    return FetchFailed(
        f"Failed to access website: no response within {DEADLINE_SECONDS} s, "
        "the time limit for one fetch (redirects included)"
    )


def content_type(header: str | None) -> tuple[str, Optional[str]]:
    """The media type and the charset of a Content-Type header value."""
    parts = (header or "").split(";")
    media_type = parts[0].strip().lower()
    charset = None
    for part in parts[1:]:
        name, _, value = part.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'")
    return media_type, charset


def is_page_type(media_type: str) -> bool:
    """True for a response type that is read as a page. A response with no
    Content-Type is read too; its bytes decide (see :func:`looks_binary`)."""
    return (
        not media_type
        or media_type.startswith(_PAGE_TYPE_PREFIXES)
        or media_type in _PAGE_TYPES
        or media_type.endswith(_PAGE_TYPE_SUFFIXES)
    )


def looks_binary(body: bytes) -> bool:
    """True when the start of ``body`` holds NUL bytes and is not UTF-16 text."""
    head = body[:1024]
    return b"\x00" in head and not head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))


def fetch_page(url: str, headers: dict | None = None, public_only: bool = False) -> Page:
    """GET ``url`` and return its body, read up to ``MAX_PAGE_BYTES`` and within
    ``DEADLINE_SECONDS`` overall.

    Redirects are followed one hop at a time and their bodies are not read.
    With ``public_only`` every hop must be a globally routable address. A body
    over the size limit, or still arriving at the deadline, comes back as far
    as it was read, with ``Page.cut`` saying so. Raises :class:`FetchRefused`,
    :class:`FetchFailed`, or the ``requests`` exception for a transport or HTTP
    error.
    """
    from backend.utils.hosts import fetch_session

    deadline = time.monotonic() + DEADLINE_SECONDS
    with fetch_session(public_only=public_only) as session:
        # Shutting the sockets down is what ends a read that is blocked on a
        # slow server; checking the clock between reads cannot.
        timer = threading.Timer(DEADLINE_SECONDS, session.connections.cut)
        timer.daemon = True
        timer.start()
        try:
            return _fetch(session, url, headers, public_only, deadline)
        except (FetchRefused, FetchFailed):
            raise
        except Exception:
            if session.connections.was_cut:
                raise _timed_out() from None
            raise
        finally:
            timer.cancel()


def _fetch(session, url: str, headers: dict | None, public_only: bool, deadline: float) -> Page:
    import requests

    from backend.utils.hosts import PrivateAddressError, private_address_reason

    max_redirects = MAX_REDIRECTS if public_only else session.max_redirects
    current = url
    for _hop in range(max_redirects + 1):
        if public_only:
            refused = private_address_reason(current)
            if refused:
                raise FetchRefused(_refusal(current, refused))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _timed_out()
        try:
            response = session.get(
                current, headers=headers, timeout=min(IDLE_SECONDS, remaining),
                allow_redirects=False, stream=True,
            )
        except requests.exceptions.ConnectionError as e:
            # The connect-time check: the name resolved differently from the
            # check above, or requests decoded the host differently.
            reason = getattr(e.args[0], "reason", None) if e.args else None
            if isinstance(reason, PrivateAddressError):
                raise FetchRefused(_refusal(
                    current,
                    f"{reason.host_name} resolves to a private or local address ({reason.address})",
                )) from None
            raise
        location = session.get_redirect_target(response)
        if not location:
            break
        response.close()  # a redirect's body is not read
        current = urljoin(current, location)
    else:
        raise FetchFailed(f"Too many redirects (more than {max_redirects})")

    try:
        response.raise_for_status()
        media_type, charset = content_type(response.headers.get("Content-Type"))
        if not is_page_type(media_type):
            raise FetchFailed(
                f"Not a web page: {current} is served as {media_type}. "
                "Only HTML, XML, JSON and plain-text responses are read."
            )
        body, cut = _read_body(session, response, deadline)
    finally:
        response.close()
    if not media_type and looks_binary(body):
        raise FetchFailed(
            f"Not a web page: {current} returned binary data with no content type. "
            "Only HTML, XML, JSON and plain-text responses are read."
        )
    return Page(url=current, body=body, media_type=media_type, charset=charset, cut=cut)


def _refusal(url: str, reason: str) -> str:
    return f"Refused to fetch {url}: {reason.rstrip('.')}. {PUBLIC_ONLY_NOTE}"


def _pieces(response):
    """The body as it arrives, decompressed, in pieces of at most ``_CHUNK_BYTES``.

    urllib3's read1 returns what one read of the socket gives. iter_content
    waits until a whole chunk has arrived, so a slow body would be held back,
    and lost if the fetch runs out of time while it waits.
    """
    raw = response.raw
    if not (hasattr(raw, "stream") and hasattr(raw, "read1")):
        yield from response.iter_content(chunk_size=_CHUNK_BYTES)
        return
    while True:
        piece = raw.read1(_CHUNK_BYTES, decode_content=True)
        if not piece:
            return
        yield piece


def _read_body(session, response, deadline: float) -> tuple[bytes, Optional[str]]:
    """The body up to ``MAX_PAGE_BYTES``, and why it stops short when it does."""
    import requests
    from urllib3.exceptions import HTTPError as TransportError

    pieces, size, over_size = [], 0, False
    try:
        for piece in _pieces(response):
            pieces.append(piece)
            size += len(piece)
            if size > MAX_PAGE_BYTES:
                over_size = True
                break
            if time.monotonic() >= deadline:
                session.connections.cut()
                break
    except Exception as e:
        if not session.connections.was_cut:
            if isinstance(e, TransportError):
                raise requests.exceptions.ConnectionError(e) from e
            raise
    body = b"".join(pieces)[:MAX_PAGE_BYTES]
    if over_size:
        return body, (
            f"Only the first {megabytes(MAX_PAGE_BYTES)} of this page were read; it is larger. "
            "The text and the counts cover that part."
        )
    if session.connections.was_cut:
        if not body:
            raise _timed_out()
        read = f"{len(body) // 1024} KB" if len(body) >= 1024 else f"{len(body)} bytes"
        return body, (
            f"The page was still arriving after {DEADLINE_SECONDS} s, so only its first {read} "
            "were read. The text and the counts cover that part."
        )
    return body, None


# Browsers read a page labelled latin-1 or ASCII as windows-1252 (WHATWG
# Encoding Standard). Python's latin-1 would turn the bytes 0x80-0x9F, where
# windows-1252 has the curly quotes, dashes and the euro sign, into control
# characters.
_READ_AS_WINDOWS_1252 = {"iso8859-1", "ascii"}
# Python codecs that decode bytes to text but are not page encodings.
_NOT_PAGE_CODECS = {"idna", "punycode", "unicode-escape", "raw-unicode-escape", "undefined"}


def _codec(label: str | None) -> Optional[str]:
    """Python's name for a charset label, or None when it cannot decode a page."""
    label = (label or "").strip().strip("\"'")
    if not label:
        return None
    try:
        name = codecs.lookup(label).name
    except (LookupError, ValueError):
        return None
    if name in _READ_AS_WINDOWS_1252:
        return "cp1252"
    return None if name in _NOT_PAGE_CODECS else name


def _decode_strict(body: bytes, encoding: str, complete: bool) -> Optional[str]:
    try:
        return body.decode(encoding)
    except UnicodeDecodeError as e:
        # A body cut at the size limit can end inside a character.
        if not complete and e.end == len(body) and len(body) - e.start < 4:
            try:
                return body[:e.start].decode(encoding)
            except UnicodeDecodeError:
                return None
        return None
    except (LookupError, ValueError):
        # An unknown label, or a codec that is not a text encoding.
        return None


def _detected_encoding(body: bytes) -> Optional[str]:
    try:
        from charset_normalizer import from_bytes
    except ImportError:
        return None
    best = from_bytes(body[:256 * 1024]).best()
    return best.encoding if best else None


def decode_page(body: bytes, http_charset: str | None = None, complete: bool = True) -> tuple[str, str]:
    """``body`` as text, and the encoding that was used.

    Tried in this order, the first that decodes every byte wins: a byte-order
    mark, the charset of the Content-Type header, the charset the page declares
    (``<meta>`` or an XML declaration), UTF-8, a guess from the bytes, and last
    windows-1252 with undecodable bytes replaced. ``complete=False`` says the
    body was cut and may end inside a character.
    """
    candidates = []
    if body.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    candidates.append(_codec(http_charset))
    try:
        from bs4.dammit import EncodingDetector
        declared = _codec(EncodingDetector.find_declared_encoding(body, is_html=True))
    except Exception:
        declared = None
    # A page whose <meta> could be read as ASCII is not UTF-16, whatever it says.
    if declared and declared.startswith(("utf-16", "utf-32")):
        declared = "utf-8"
    candidates += [declared, "utf-8"]

    tried = set()
    for encoding in candidates:
        if not encoding or encoding in tried:
            continue
        tried.add(encoding)
        text = _decode_strict(body, encoding, complete)
        if text is not None:
            return text, encoding
    guess = _codec(_detected_encoding(body))
    if guess and guess not in tried:
        text = _decode_strict(body, guess, complete)
        if text is not None:
            return text, guess
    return body.decode("cp1252", errors="replace"), "cp1252"
