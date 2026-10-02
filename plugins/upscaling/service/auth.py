"""Bearer token and Host check for the Upscaling plugin.

Both come from backend/utils/sidecar_guard.py, loaded by path because this
service runs outside the backend package. The token is the one in
data/.upscaling_internal_secret, which the backend reads from the same file
(backend/api/upscaling_api.py); no reply carries it.
"""
import importlib.util
import sys
from pathlib import Path

from fastapi import HTTPException, Request

TOKEN_NAME = "upscaling"
_GUARD_MODULE = "guaardvark_sidecar_guard"


def _load_guard():
    loaded = sys.modules.get(_GUARD_MODULE)
    if loaded is not None:
        return loaded
    path = Path(__file__).resolve().parents[3] / "backend" / "utils" / "sidecar_guard.py"
    spec = importlib.util.spec_from_file_location(_GUARD_MODULE, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules[_GUARD_MODULE] = module
    return module


guard = _load_guard()


def auth_token() -> str:
    """The token the backend sends, created on first use."""
    return guard.internal_token(TOKEN_NAME)


def verify_token(request: Request):
    """Check Authorization: Bearer <token> header.

    Raise 401 if missing or invalid.
    """
    if not guard.bearer_matches(TOKEN_NAME, request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")
