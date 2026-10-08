"""Is ComfyUI down, or only busy?

While ComfyUI loads a large model or samples on a pegged GPU, its HTTP handler
can stop answering for tens of seconds, yet the process is alive and the render
is fine. The connection is still accepted during that time and only the reply
is late; a ComfyUI that has exited refuses the connection. So a read timeout
means busy, and only a failed connection means down.
"""

from __future__ import annotations

UP = "up"
BUSY = "busy"
DOWN = "down"


def probe_comfyui(url: str, http_timeout: float = 2.0, connect_timeout: float = 1.0) -> str:
    """UP when ComfyUI answers HTTP 200, BUSY when it accepts the connection but
    does not answer in time (or answers with an error), DOWN when the
    connection is refused or cannot be made."""
    import requests

    try:
        resp = requests.get(url, timeout=(connect_timeout, http_timeout))
    except requests.exceptions.ConnectTimeout:
        return DOWN
    except requests.exceptions.ReadTimeout:
        return BUSY
    except requests.exceptions.ConnectionError:
        return DOWN
    except requests.exceptions.RequestException:
        return BUSY
    return UP if resp.status_code == 200 else BUSY
