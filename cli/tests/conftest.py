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
import os
import sys
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


# --- e2e: the real backend, in-process -------------------------------------
#
# `inprocess_backend` boots the actual Flask app and routes the CLI's HTTP client
# into Flask's test client, so a smoke test exercises the real routes without a
# socket, a subprocess, or a port to leak. The tier's CI home is a job that already
# has the backend stack installed (see docs/CLI_PLAN.md sections 4.5 and 5).


class InProcessBackend:
    """The real Flask app, answering httpx requests through Flask's test client.

    Deliberately not a fake: nothing here decides what a route returns, so a smoke
    test that passes against it has proved the CLI and the backend agree end to end.

    ``calls`` records ``(method, path)`` for every request Flask served. The transport
    is address-agnostic, so a command that answers entirely in-process (or whose
    request silently went nowhere) would still exit 0; asserting the expected route
    was actually called is what makes a smoke test mean "the backend answered this".
    """

    # MockTransport never dials this, but relative request URLs need a base_url and a
    # few commands print `server_url`, so it has to be a parseable URL rather than None.
    server_url = "http://inprocess.local"

    def __init__(self, app) -> None:
        self.app = app
        self.calls: list[tuple[str, str]] = []
        self._client = app.test_client()

    def called(self, method: str, path: str) -> bool:
        return (method.upper(), path) in self.calls

    def paths(self) -> list[str]:
        return [path for _method, path in self.calls]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        query = request.url.query
        if query:
            path += "?" + (query.decode() if isinstance(query, bytes) else str(query))
        response = self._client.open(
            path=path,
            method=request.method,
            data=request.content,
            headers={
                key: value
                for key, value in request.headers.items()
                if key.lower() not in ("host", "content-length")
            },
        )
        return httpx.Response(
            response.status_code,
            content=response.get_data(),
            headers=dict(response.headers),
        )


def _normalise_dsn(dsn: str) -> str:
    """Drop any SQLAlchemy driver suffix so `postgresql+psycopg2://` matches `postgresql://`."""
    scheme, sep, rest = dsn.partition("://")
    if not sep:
        return dsn
    return f"{scheme.split('+', 1)[0]}://{rest}"


def _same_database(effective: str, wanted: str) -> bool:
    """Whether two DSNs touch the same database (host + path decide that, credentials do not)."""
    from urllib.parse import urlsplit

    e, w = urlsplit(_normalise_dsn(effective)), urlsplit(_normalise_dsn(wanted))
    # The port matters: the same database name on a different port is a different server,
    # and omitting it would let the scratch-DSN guard pass while bound to the dev database.
    return (e.hostname, e.port or 5432, e.path) == (w.hostname, w.port or 5432, w.path)


def _assert_reachable(dsn: str) -> None:
    """Fail loudly when the scratch DSN is set but no server answers it."""
    try:
        import psycopg2
    except ImportError:  # pragma: no cover - backend stack implies psycopg2
        pytest.fail("GUAARDVARK_E2E_DATABASE_URL is set but psycopg2 is not installed")
    try:
        # psycopg2 rejects SQLAlchemy's driver suffix (`postgresql+psycopg2://`), so hand it
        # the normalised DSN; otherwise the failure message blames the server, not the scheme.
        psycopg2.connect(_normalise_dsn(dsn), connect_timeout=5).close()
    except Exception as exc:
        pytest.fail(f"GUAARDVARK_E2E_DATABASE_URL is set but unreachable: {exc}")


def _e2e_database_url() -> str:
    """The scratch database the e2e tier may boot against, or a skip.

    `backend/config.py` rejects sqlite and resolves `DATABASE_URL` to Postgres at
    import time, and the `guaardvark` role cannot create databases -- so a missing
    override would silently boot against the live dev database. An explicit scratch
    DSN is therefore required and is reachability-checked; only
    `GUAARDVARK_E2E_ALLOW_CONFIGURED_DB=1` opts into whatever the environment already
    resolves, which is meant for local validation against a database you are willing
    to let the app run its idempotent `create_all()`/seed pass over.
    """
    explicit = os.environ.get("GUAARDVARK_E2E_DATABASE_URL", "").strip()
    if explicit:
        # `backend/config.py` keeps only the bare `postgresql`/`postgres` scheme and sends
        # anything else to the configured database, so a driver-suffixed DSN must be
        # normalised here rather than passed through -- left raw it would silently bind the
        # wrong database (and then fail the `_same_database` guard blaming the server). A
        # scheme that is not Postgres at all is rejected outright, naming what works.
        database_url = _normalise_dsn(explicit)
        scheme = database_url.partition("://")[0].lower()
        if scheme not in ("postgresql", "postgres"):
            pytest.fail(
                "GUAARDVARK_E2E_DATABASE_URL must name a PostgreSQL server "
                f"(postgresql:// or postgres://); got scheme {scheme!r}"
            )
        _assert_reachable(database_url)
        return database_url
    if os.environ.get("GUAARDVARK_E2E_ALLOW_CONFIGURED_DB") == "1":
        return ""  # leave DATABASE_URL as backend/config.py already resolves it
    pytest.skip(
        "e2e needs GUAARDVARK_E2E_DATABASE_URL -- a scratch Postgres database. "
        "backend/config.py rejects sqlite and the `guaardvark` role cannot create "
        "databases, so a missing override would silently test the live dev database. "
        "Set GUAARDVARK_E2E_ALLOW_CONFIGURED_DB=1 to opt into the configured database."
    )


@pytest.fixture(scope="session")
def inprocess_backend(tmp_path_factory):
    """A running Guaardvark backend, in this process, with the CLI pointed at it.

    Session-scoped because booting the app costs seconds and nothing in the tier
    mutates it. The environment has to be set *before* the first `backend.*` import
    (`config.py` reads it at import time, and `backend/app.py` runs `create_app()` at
    module level), hence the manual `pytest.MonkeyPatch` rather than the
    function-scoped `monkeypatch` fixture. The CLI suite never imports backend, so
    setting it here is safe.

    Hermetic by construction: `GUAARDVARK_ROOT` is a throwaway tmp directory (so the
    app's own data dirs and its idempotent `create_all()`/seed pass never touch the
    repo or the dev database), `GUAARDVARK_STORAGE_DIR` is pinned to that same tmp root
    because call-time storage readers (`system_mapper`, `code_storage_bridge`) consult
    it *before* falling back to the root that is restored below, and the plugin state
    store is re-pointed at a tmp file (see below) so no plugin listed `running` in the
    developer's `data/plugin_state.json` is restored and that file is not rewritten.
    `GUAARDVARK_MODE=test` additionally redirects the app's writable data directories.
    The module-level `_PluginRunnerClient`
    sidecar process still starts at import -- it is a lightweight runner *client*, not a
    GPU plugin -- and the tier is designed to run in its own pytest session (`-m e2e`) so
    the tmp root cannot leak into other tiers.
    """
    pytest.importorskip("flask", reason="backend stack not installed (CLI-only job)")
    database_url = _e2e_database_url()

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    patch = pytest.MonkeyPatch()
    patch.setenv("GUAARDVARK_MODE", "test")
    # start.sh normally verifies migrations and that llama is reachable; neither is
    # available here, and both are checks, not behaviour the tier depends on.
    patch.setenv("PYTEST_SKIP_MIGRATION_CHECK", "1")
    patch.setenv("PYTEST_SKIP_LLAMA_CHECK", "1")

    original_root = os.environ.get("GUAARDVARK_ROOT")
    tmp_root = tmp_path_factory.mktemp("guaardvark_e2e_root")
    patch.setenv("GUAARDVARK_ROOT", str(tmp_root))
    # Call-time storage readers (`system_mapper._store_dir`, `code_storage_bridge`) take
    # `GUAARDVARK_STORAGE_DIR` first and only then fall back to `<GUAARDVARK_ROOT>/data`.
    # The root env var is restored below so later tiers see the project, which would send
    # them back to the repo; pin the storage dir to the tmp root instead. Left set for the
    # session (not restored) because it is read at call time, not import time.
    os.environ["GUAARDVARK_STORAGE_DIR"] = str(tmp_root / "data")
    if database_url:
        patch.setenv("DATABASE_URL", database_url)

    # `plugin_state.json` is anchored to `<repo>/data` (PluginRegistry default
    # plugins_dir is `backend/plugins/../../plugins`, and PluginManager derives the
    # store path from `plugins_dir.parent`), NOT to GUAARDVARK_ROOT. A tmp root alone
    # therefore still lets `PluginManager._init_plugin_status` read the developer's
    # `running` list -- restoring real services on boot -- and rewrite the file. Point
    # the store at the session tmp dir *before* `get_plugin_manager()` first runs: no
    # plugin is restored and nothing under the repo is written.
    import backend.plugins.plugin_manager as plugin_manager

    from backend.plugins.plugin_state_store import PluginStateStore

    state_path = tmp_path_factory.mktemp("guaardvark_e2e_plugin_state") / "plugin_state.json"

    class _IsolatedPluginStateStore(PluginStateStore):
        def __init__(self, path=None):  # ignore the repo-anchored default path
            super().__init__(state_path)

    patch.setattr(plugin_manager, "PluginStateStore", _IsolatedPluginStateStore)
    # Defensive: if something already built the global manager, re-point its store too.
    _existing_manager = getattr(plugin_manager, "_manager", None)
    if _existing_manager is not None:
        _existing_manager.state_store = _IsolatedPluginStateStore()

    import backend.app as backend_app

    # `backend/config.py` captured the tmp root at import (and wrote it back into the
    # environment), so the app is bound to it. Restore the *env var* now: later tests
    # in the same session that resolve the project from `GUAARDVARK_ROOT` at call time
    # (`cli/llx/commands/recipes.py`) must see the original value, not the tmp one.
    # The app keeps using `config.GUAARDVARK_ROOT` (already captured), so this is safe.
    if original_root is None:
        os.environ.pop("GUAARDVARK_ROOT", None)
    else:
        os.environ["GUAARDVARK_ROOT"] = original_root

    # `DATABASE_URL` is read once at import; if `backend.config` had already been
    # imported, setting the env var is a silent no-op and the app is bound to whatever
    # it resolved earlier -- possibly the live dev database. Prove it took.
    if database_url:
        effective = backend_app.app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not _same_database(effective, database_url):
            pytest.fail(
                "scratch DSN not honoured -- backend.config was imported before the "
                f"fixture set DATABASE_URL (app is bound to {effective!r}, "
                f"wanted {database_url!r})"
            )

    backend = InProcessBackend(backend_app.app)

    from llx.client import LlxClient

    real_init = LlxClient.__init__

    def patched_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.http = httpx.Client(
            base_url=self.server_url,
            transport=httpx.MockTransport(backend.handler),
        )

    patch.setattr(LlxClient, "__init__", patched_init)
    yield backend
    patch.undo()


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
