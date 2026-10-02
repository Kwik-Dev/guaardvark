"""Inline image preview and local audio playback with terminal fallbacks."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

from llx.termcaps import detect


def _write_tty(payload: str) -> None:
    try:
        with open("/dev/tty", "w", encoding="utf-8") as tty:
            tty.write(payload)
            tty.flush()
            return
    except OSError:
        pass
    sys.stdout.write(payload)
    sys.stdout.flush()


def _kitty_preview(data: bytes) -> bool:
    b64 = base64.standard_b64encode(data).decode("ascii")
    # f=100 = PNG; JPEG also often works when the decoder is liberal, but we
    # prefer PNG. Callers should pass PNG when they can.
    chunk_size = 4096
    first = True
    # Drawn at its own pixel size, a 1024 px render runs off most screens; a
    # height in rows scales it to fit, and kitty keeps the aspect ratio.
    rows = max(8, int(shutil.get_terminal_size((100, 40)).lines * 0.55))
    for i in range(0, len(b64), chunk_size):
        chunk = b64[i : i + chunk_size]
        more = 1 if i + chunk_size < len(b64) else 0
        if first:
            _write_tty(f"\x1b_Ga=T,f=100,r={rows},m={more};{chunk}\x1b\\")
            first = False
        else:
            _write_tty(f"\x1b_Gm={more};{chunk}\x1b\\")
    _write_tty("\n")
    return True


def _iterm_preview(data: bytes) -> bool:
    b64 = base64.standard_b64encode(data).decode("ascii")
    _write_tty(f"\x1b]1337;File=inline=1;size={len(data)}:{b64}\x07\n")
    return True


def _chafa_preview(path: Path) -> bool:
    chafa = shutil.which("chafa")
    if not chafa:
        return False
    try:
        subprocess.run([chafa, "--size=80x24", str(path)], check=False)
        return True
    except OSError:
        return False


def preview_image(source: str | Path | bytes, console=None) -> str:
    """Render an image in-terminal. Returns the protocol used (or 'path')."""
    caps = detect()
    path: Path | None = None
    data: bytes
    if isinstance(source, (str, Path)):
        path = Path(source)
        data = path.read_bytes()
    else:
        data = source

    if caps.graphics == "kitty":
        try:
            _kitty_preview(data)
            return "kitty"
        except Exception:
            pass
    if caps.graphics == "iterm":
        try:
            _iterm_preview(data)
            return "iterm"
        except Exception:
            pass
    if path is not None and _chafa_preview(path):
        return "chafa"

    shown = str(path) if path is not None else "(in-memory image)"
    if console is not None:
        if path is not None:
            console.print(f"[link=file://{path}]{shown}[/link]")
        else:
            console.print(f"[llx.dim]{shown}[/llx.dim]")
    return "path"


def fetch_media(url: str, server: str = "", timeout: float = 60.0) -> bytes | None:
    """Bytes of a file the server made, from a full URL or a server-relative one."""
    import httpx

    from llx.config import get_api_key, get_server_url

    if not url.startswith(("http://", "https://")):
        url = (server or get_server_url()).rstrip("/") + "/" + url.lstrip("/")
    api_key = get_api_key(url)
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
        return resp.content if resp.status_code == 200 and resp.content else None
    except httpx.HTTPError:
        return None


def show_generated(items, server: str = "", console=None) -> int:
    """Draw the pictures a chat turn or /imagine made; name the rest.

    `items` is the `generated_images` list a chat reply carries ({url, type}).
    The files live on the server, so they are fetched first; this also works
    when the CLI talks to Guaardvark on another machine. Returns how many were drawn.
    """
    drawn = 0
    for item in items or []:
        url = (item or {}).get("url") if isinstance(item, dict) else None
        if not url:
            continue
        full = url if url.startswith("http") else (server.rstrip("/") + url if server else url)
        if (item.get("type") or "image") == "image" and detect().graphics in ("kitty", "iterm"):
            data = fetch_media(url, server)
            if data:
                preview_image(data, console=console)
                drawn += 1
                continue
        if console is not None:
            console.print(f"[link={full}]{full}[/link]")
    return drawn


def play_audio(path: str | Path, *, no_play: bool = False) -> str | None:
    """Play a local audio file. Returns the player used, or None."""
    if no_play:
        return None
    target = str(Path(path))
    if not Path(target).exists():
        return None
    for cmd in (
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", target],
        ["paplay", target],
        ["afplay", target],
        ["aplay", target],
        ["mpv", "--no-video", "--really-quiet", target],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return cmd[0]
            except OSError:
                continue
    return None


def extract_media_path(payload: dict, server: str = "") -> str | None:
    """Best-effort path/URL from a generation API payload."""
    if not isinstance(payload, dict):
        return None
    for key in ("path", "file_path", "filepath", "output_path", "image_path", "audio_path"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    url = payload.get("audio_url") or payload.get("url") or payload.get("image_url")
    if isinstance(url, str) and url:
        if url.startswith("http"):
            return url
        if server:
            return server.rstrip("/") + url
        return url
    response = payload.get("response")
    if isinstance(response, str) and ("/" in response or response.endswith((".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3"))):
        return response.strip()
    return None
