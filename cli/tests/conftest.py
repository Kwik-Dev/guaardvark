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
import json
from pathlib import Path

import httpx
import pytest

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# --- tiering ---------------------------------------------------------------

_MARKERS = (
    ("unit", "fast, no I/O (the default)"),
    ("contract", "JSON shape and surface invariants"),
    ("e2e", "boots something real; no GPU and no live backend required"),
)


def pytest_configure(config):
    for name, description in _MARKERS:
        config.addinivalue_line("markers", f"{name}: {description}")


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the golden JSON snapshots instead of asserting against them",
    )

_CONTRACT_PATTERNS = (
    "test_*json_contracts.py",
    "test_spec_parity.py",
    "test_command_catalog_contract.py",
    "test_plugins_gpu_json.py",
    "test_*golden*.py",
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
        # The query string is not part of `url.path`, and a fork command may legitimately
        # put one there (`api request --query`), so the full URL is kept alongside.
        self.full_urls: list[str] = []
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
        self.full_urls.append(str(request.url))
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


# --- golden --json snapshots -----------------------------------------------

# Keys whose value legitimately changes between runs (a clock, a measured span, the
# server the CLI happened to talk to). Their *presence* is contract; their value is
# not, so the value is replaced and a renamed/removed key still fails.
_VOLATILE_KEYS = frozenset(
    {
        "timestamp",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "completed_at",
        "duration",
        "duration_seconds",
        "elapsed",
        "elapsed_seconds",
        "server_url",
        "base_url",
        "config_file",
        "config_dir",
        "home",
        "cwd",
    }
)


def _normalize(value, paths):
    """Recursively make a payload machine-independent. ``paths`` are temp-dir prefixes
    the isolated home created, replaced so a golden file is identical on every box.

    A volatile key's *value* is replaced, but the replacement names the value's type:
    a script parsing ``created_at`` depends on it being a string, so a string becoming
    an int must still fail.
    """
    if isinstance(value, dict):
        return {
            key: (f"<volatile:{type(item).__name__}>" if key in _VOLATILE_KEYS
                  else _normalize(item, paths))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize(item, paths) for item in value]
    if isinstance(value, str):
        for prefix in paths:
            if prefix and prefix in value:
                return value.replace(prefix, "<tmp>")
        return value
    return value


def _diff(expected, actual, path="$"):
    """A path-annotated difference, so a shape regression names the key that moved."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                lines.append(f"  + {path}.{key} = {actual[key]!r}")
            elif key not in actual:
                lines.append(f"  - {path}.{key} (was {expected[key]!r})")
            else:
                lines.append(_diff(expected[key], actual[key], f"{path}.{key}"))
        return "\n".join(line for line in lines if line)
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return f"  ~ {path}: {len(expected)} item(s) -> {len(actual)} item(s)"
        return "\n".join(
            line for line in (_diff(e, a, f"{path}[{i}]") for i, (e, a) in enumerate(zip(expected, actual))) if line
        )
    if expected != actual:
        return f"  ~ {path}: {expected!r} -> {actual!r}"
    return ""


@pytest.fixture
def golden(pytestconfig, isolated_home):
    """Compare a ``--json`` payload against ``cli/tests/golden/<name>.json``.

    Pass a ``CliRunner`` result (the preferred form — it also asserts the exit code)
    or an already-parsed payload. Rewrite the files deliberately with
    ``pytest cli/tests --update-golden``; review the diff before committing it. The
    point is to catch *shape* drift — a renamed or dropped key breaks every script
    that reads it — which ad-hoc asserts only catch where someone remembered to look.
    """
    update = bool(pytestconfig.getoption("--update-golden"))
    replacements = [str(isolated_home), str(isolated_home.parent)]

    def _check(name, payload):
        if hasattr(payload, "output"):  # a CliRunner result
            assert payload.exit_code == 0, payload.output
            try:
                payload = json.loads(payload.output)
            except (TypeError, ValueError):  # pragma: no cover - a broken command
                raise AssertionError(f"{name}: --json did not emit JSON:\n{payload.output}")
        normalized = _normalize(payload, replacements)
        path = GOLDEN_DIR / f"{name}.json"
        if update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n")
            return normalized
        assert path.exists(), (
            f"no golden file for {name!r} ({path}). "
            "Run `pytest cli/tests --update-golden`, then review and commit the file."
        )
        expected = json.loads(path.read_text())
        if expected != normalized:
            raise AssertionError(
                f"golden mismatch for {name!r} ({path}):\n"
                + _diff(expected, normalized)
                + "\n\nIf this shape change is intended, run `pytest cli/tests --update-golden` "
                "and commit the regenerated file."
            )
        return normalized

    return _check
