"""Where the swarm reaches the main Guaardvark backend.

The same resolution as backend/utils/backend_http.py (sidecars do not import
the backend): FLASK_PORT from the environment when the swarm inherits it;
else the FLASK_PORT start.sh records in this checkout's .env (macOS moves the
backend to 5055); else 5000, the port the backend listens on by default.
GPU holds, VRAM reads, merge requests, event pushes and the agents' bus URLs
all go through here.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_FLASK_PORT = "5000"

# plugins/swarm/service/backend_url.py -> the checkout root
CHECKOUT_ENV = Path(__file__).resolve().parents[3] / ".env"


def _checkout_env_value(key: str) -> str:
    """``key`` from this checkout's .env (last line wins), or ""."""
    value = ""
    try:
        for line in CHECKOUT_ENV.read_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return value


def backend_port() -> str:
    return (
        (os.environ.get("FLASK_PORT") or "").strip()
        or _checkout_env_value("FLASK_PORT")
        or DEFAULT_FLASK_PORT
    )


def backend_api_url() -> str:
    """The backend API base, e.g. ``http://localhost:5000/api``."""
    return f"http://localhost:{backend_port()}/api"
