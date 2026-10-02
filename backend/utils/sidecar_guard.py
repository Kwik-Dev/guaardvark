"""What Guaardvark's own plugin servers share with the backend: its Host rule,
and a token file for the routes only the backend may call.

The Host rule is backend/utils/host_check.py's, which also lists every server
that applies it. Its adapters are re-exported here: HostCheckASGIMiddleware
(FastAPI, Starlette, uvicorn), aiohttp_middleware (aiohttp) and
HostCheckMiddleware (WSGI, Flask).

A plugin server runs with only its own folder on sys.path and in its own
virtualenv, so it cannot import the backend package, and must not:
backend/__init__.py starts Socket.IO and backend/utils/__init__.py loads the
LLM stack. It loads this file by path instead::

    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "sidecar_guard.py"
    spec = importlib.util.spec_from_file_location("guaardvark_sidecar_guard", path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)

and this file then loads host_check.py, and through it cors_policy.py, as
members of a stand-in package that has backend/utils as its folder and runs
no __init__. The three files import only the standard library and each other;
backend/tests/test_sidecar_host_check.py loads them with the backend package
blocked to keep it that way.

Token files: a plugin that only the backend calls and whose routes act on
files or devices (upscaling, vision pipeline) refuses every route but /health
without a bearer token (BearerTokenASGIMiddleware). The token lives in
data/.<plugin>_internal_secret, readable by this user only, and both sides
read it from there, so neither a reply nor the network carries it. The plugin
creates it when missing (internal_token); the backend only reads it
(read_internal_token): a plugin that is not running has nothing to call. The
swarm plugin keeps its own data/.swarm_internal_secret and gate.
"""

from __future__ import annotations

import hmac
import importlib
import os
import re
import secrets
import sys
import tempfile
import types
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_STAND_IN = "guaardvark_backend_utils"


def _standalone(name: str):
    """backend/utils/<name>.py as a member of the stand-in package."""
    package = sys.modules.get(_STAND_IN)
    if package is None:
        package = types.ModuleType(_STAND_IN)
        package.__path__ = [str(_HERE)]
        sys.modules[_STAND_IN] = package
    return importlib.import_module(f"{_STAND_IN}.{name}")


if __package__:
    from . import host_check
else:
    host_check = _standalone("host_check")

HostCheckASGIMiddleware = host_check.HostCheckASGIMiddleware
HostCheckMiddleware = host_check.HostCheckMiddleware
aiohttp_middleware = host_check.aiohttp_middleware
host_allowed = host_check.host_allowed

DATA_DIR = _HERE.parents[1] / "data"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def token_path(name: str) -> Path:
    if not _NAME.match(name or ""):
        raise ValueError(f"not a plugin name: {name!r}")
    return DATA_DIR / f".{name}_internal_secret"


def read_internal_token(name: str) -> str:
    """The plugin's token, or "" when the plugin has not created it yet."""
    try:
        return token_path(name).read_text().strip()
    except OSError:
        return ""


def internal_token(name: str) -> str:
    """The plugin's token, created on first use. A new file appears whole
    (written aside, then linked into place), so a reader never sees it empty,
    and of two processes creating it at once the second reads the first's."""
    existing = read_internal_token(name)
    if existing:
        return existing
    path = token_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=f".{name}_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_urlsafe(32))
        try:
            os.link(staged, path)
        except FileExistsError:
            pass
        except OSError:
            # A file system without hard links.
            if not path.exists():
                os.replace(staged, path)
    finally:
        try:
            os.unlink(staged)
        except OSError:
            pass
    return read_internal_token(name)


def bearer_matches(name: str, authorization: Optional[str]) -> bool:
    """Whether an Authorization header carries the plugin's token."""
    value = (authorization or "").strip()
    if not value.startswith("Bearer "):
        return False
    expected = internal_token(name)
    return bool(expected) and hmac.compare_digest(value[7:].encode(), expected.encode())


class BearerTokenASGIMiddleware:
    """ASGI middleware that refuses, with 401, every request and websocket
    not carrying the plugin's token, except a GET or HEAD of ``open_paths``:
    /health, which scripts/start.sh and the Plugins page probe without it and
    which reports status only. Add it before the Host check, so that check
    runs first:

        app.add_middleware(guard.BearerTokenASGIMiddleware, name="upscaling")
        app.add_middleware(guard.HostCheckASGIMiddleware)

    Every other caller is the backend, which sends the token from the file.
    """

    def __init__(self, app, name: str, open_paths=("/health",)):
        token_path(name)
        self.app = app
        self.name = name
        self.open_paths = frozenset(open_paths)

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if kind == "http" and scope.get("method") in ("GET", "HEAD") and scope.get("path") in self.open_paths:
            await self.app(scope, receive, send)
            return
        headers = scope.get("headers") or ()
        values = [v.decode("latin-1") for k, v in headers if k.lower() == b"authorization"]
        if len(values) == 1 and bearer_matches(self.name, values[0]):
            await self.app(scope, receive, send)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail": "Invalid or missing bearer token"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
