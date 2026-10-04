"""Shared fixtures for the CLI suite.

Before this file every test module hand-rolled its own fake backend, so each one
differed slightly and the suite had no common notion of "a call the CLI made". These
fixtures are additive: no upstream test file was changed to accommodate them. (The
same change does modify one upstream test, `test_local_tools.py`, to stop assuming a
`python` on PATH — unrelated to the fixtures.)

Tiers (registered here, applied automatically by filename so upstream test files never
need editing):

* ``unit``      the default
* ``contract``  `--json` shape and surface invariants
* ``e2e``       boots something real; no GPU, no live backend

Run the fast tiers with `-m "not e2e"`.

Markers are registered in `pytest_configure` rather than a `pytest.ini` because this
repo's .gitignore ignores `pytest.ini` ("local-only dev tooling config"), so an ini file
here would never reach CI.
"""
from __future__ import annotations

import fnmatch

import httpx
import pytest

# --- tiering ---------------------------------------------------------------

_MARKERS = (
    ("unit", "fast, no I/O (the default)"),
    ("contract", "JSON shape and surface invariants"),
    ("e2e", "boots something real; no GPU and no live backend required"),
)


def pytest_configure(config):
    for name, description in _MARKERS:
        config.addinivalue_line("markers", f"{name}: {description}")

_CONTRACT_PATTERNS = (
    "test_*json_contracts.py",
    "test_spec_parity.py",
    "test_command_catalog_contract.py",
    "test_plugins_gpu_json.py",
)
_E2E_PATTERNS = ("test_*_e2e.py",)


def pytest_collection_modifyitems(items):
    """Mark every test by its file name, so upstream test files stay untouched."""
    for item in items:
        name = item.path.name
        if any(fnmatch.fnmatch(name, p) for p in _E2E_PATTERNS):
            item.add_marker(pytest.mark.e2e)
        elif any(fnmatch.fnmatch(name, p) for p in _CONTRACT_PATTERNS):
            item.add_marker(pytest.mark.contract)
        else:
            item.add_marker(pytest.mark.unit)


# --- fixtures --------------------------------------------------------------


class FakeBackend:
    """A minimal stand-in for the Guaardvark backend.

    Routes are matched on (method, path) exactly. Anything unmatched is a 404 with a
    message naming the route, so a test that forgets to declare one fails loudly
    instead of silently getting an empty body.

    Every request the CLI makes is recorded in ``calls``, which is what lets a test
    assert *what was sent* — e.g. that the music-video commands never POST to an
    approval route.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []
        self._routes: list[tuple[str, str, int, object, str | None]] = []
        self._default: tuple[int, object] | None = None

    def route(self, method: str, path: str, *, status: int = 200, json: object = None,
              text: str | None = None) -> "FakeBackend":
        self._routes.append((method.upper(), path, status, json, text))
        return self

    def default(self, *, status: int = 200, json: object = None) -> "FakeBackend":
        """Answer any unmatched route. For assertions of the form "this command
        performs no writes", where the response body is irrelevant."""
        self._default = (status, json if json is not None else {})
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path, request.content))
        for method, path, status, payload, text in self._routes:
            if request.method == method and request.url.path == path:
                if text is not None:
                    return httpx.Response(status, text=text)
                return httpx.Response(status, json=payload if payload is not None else {})
        if self._default is not None:
            status, payload = self._default
            return httpx.Response(status, json=payload)
        return httpx.Response(
            404,
            json={"error": f"fake backend has no route for {request.method} {request.url.path}"},
        )

    # -- assertions helpers ------------------------------------------------

    def calls_for(self, method: str | None = None, path: str | None = None):
        return [
            c for c in self.calls
            if (method is None or c[0] == method.upper())
            and (path is None or c[1] == path)
        ]

    def posted_paths(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "POST"]


@pytest.fixture
def fake_backend(monkeypatch) -> FakeBackend:
    """Replace the CLI's transport with an in-process httpx MockTransport.

    Patched at `LlxClient.__init__` rather than at `get_client`, because command
    modules do `from llx.client import get_client` at import time — patching the
    factory would miss those already-bound names, while patching the constructor
    catches every client however it was obtained.
    """
    from llx.client import LlxClient

    backend = FakeBackend()
    real_init = LlxClient.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.http = httpx.Client(
            base_url=self.server_url,
            transport=httpx.MockTransport(backend.handler),
        )

    monkeypatch.setattr(LlxClient, "__init__", patched_init)
    return backend


@pytest.fixture
def cli_runner():
    """A Typer/Click CliRunner."""
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point the CLI's config at a throwaway home so tests cannot read or write the
    developer's real config.

    `HOME` alone is not enough: `llx.config` computes `CONFIG_FILE` from
    `Path.home()` at import time, and by the time a test runs the module is already
    imported. So the resolved paths are re-pointed too, and `HOME` is set for any
    subprocess a test spawns.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    try:
        from llx import config
    except Exception:  # pragma: no cover - config is always importable in practice
        return tmp_path

    home = tmp_path / ".guaardvark"
    legacy = tmp_path / ".llx"
    for name, value in (
        ("GUAARDVARK_DIR", home),
        ("CONFIG_DIR", home),
        ("CONFIG_FILE", home / "cli.json"),
        ("RUNTIME_FILE", home / "runtime.json"),
        ("LEGACY_CONFIG_DIR", legacy),
        ("LEGACY_CONFIG_FILE", legacy / "config.json"),
        ("LEGACY_SESSIONS_FILE", legacy / "sessions.json"),
        ("LEGACY_HISTORY_FILE", legacy / "history"),
    ):
        if hasattr(config, name):
            monkeypatch.setattr(config, name, value, raising=False)
    return tmp_path
