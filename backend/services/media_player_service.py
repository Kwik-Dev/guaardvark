#!/usr/bin/env python3
"""
Media Player Service - MPRIS2 control via gdbus, VLC launch, and music file search.

Linux only: players are reached over the D-Bus session bus with gdbus, the
system volume through ALSA's amixer, and music plays in a Linux VLC build. The
media tools check backend.utils.platform.media_player_available() before
calling in here, and are not registered elsewhere.
"""

import fcntl
import logging
import os
import re
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MEDIA_CONTROL_ENABLED = os.getenv("GUAARDVARK_MEDIA_CONTROL", "true").lower() == "true"

MUSIC_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac", ".opus", ".wma"}

VLC_PATH = "/snap/bin/vlc"

MPRIS2_BUS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS2_OBJECT_PATH = "/org/mpris/MediaPlayer2"
MPRIS2_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS2_PROPS_IFACE = "org.freedesktop.DBus.Properties"
DBUS_NAME = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"

NO_PLAYER = "No media player is running."

# Why a player was picked when the caller named none; shown in tool results.
CHOSEN_GUAARDVARK = "the VLC Guaardvark started"
CHOSEN_ONLY = "the only player open"
CHOSEN_PLAYING = "the only one playing"

# A player name as MPRIS2 uses it: the D-Bus name after the prefix, such as
# 'vlc', 'spotify' or 'vlc.instance4242'.
_PLAYER_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")


class PlayerChoiceNeeded(RuntimeError):
    """Several players are open and none is the one to use without being named.
    `players` holds (name, status) pairs for the caller to list."""

    def __init__(self, message: str, players: List[Tuple[str, str]]):
        super().__init__(message)
        self.players = players


# ===== GVariant text =====
# `gdbus call` prints its reply with g_variant_print(value, TRUE). The reader
# parses that text into Python values, so a metadata field is found by its key
# in the dictionary and text inside another field is never taken for a key: a
# track whose album is "'xesam:title': <'X'>" keeps its own title.

# GLib writes control characters as \n \t \r \a \b \f \v, other unprintable
# characters as \uXXXX or \UXXXXXXXX, and puts a backslash before \ ' and ".
_GV_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}
_GV_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)", re.DOTALL)

# Bytestrings (b'...') are escaped by g_strescape: the same letters, and octal
# \ooo for every other byte below 0x20 or from 0x7f up.
_GV_BYTE_ESCAPES = {ord(k): v.encode() for k, v in _GV_ESCAPES.items()}
_GV_BYTE_ESCAPE = re.compile(rb"\\([0-7]{1,3}|.)", re.DOTALL)

# A type keyword is followed by its value: <int64 240600860>, <objectpath '/x'>.
_GV_TYPE_WORDS = frozenset({"boolean", "byte", "int16", "uint16", "int32", "uint32", "handle",
                            "int64", "uint64", "double", "string", "objectpath", "signature"})
_GV_NUMBER = re.compile(
    r"[-+]?(?:0[xX][0-9a-fA-F]+|inf\b|nan\b|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)")
_GV_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_GV_BASIC_TYPES = frozenset("bynqiuxthdsogv*?r")
_GV_MAX_DEPTH = 64


def _gv_unescape(raw: str) -> str:
    def one(match: "re.Match") -> str:
        esc = match.group(1)
        if len(esc) > 1:
            code = int(esc[1:], 16)
            return chr(code) if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "�"
        return _GV_ESCAPES.get(esc, esc)
    return _GV_ESCAPE.sub(one, raw)


def _gv_unescape_bytes(raw: str) -> str:
    def one(match: "re.Match") -> bytes:
        esc = match.group(1)
        if 0x30 <= esc[0] <= 0x37:  # an octal digit
            return bytes([int(esc, 8) & 0xFF])
        return _GV_BYTE_ESCAPES.get(esc[0], esc)
    data = _GV_BYTE_ESCAPE.sub(one, raw.encode("utf-8", "surrogateescape"))
    return data.decode("utf-8", "replace")


class _GVariantReader:
    """Reads one value in GVariant text form. Tuples become tuples, arrays lists,
    dictionaries dicts, strings, object paths and signatures str, bytestrings str
    (decoded as UTF-8), 'nothing' None; a variant becomes the value it holds.
    Raises ValueError on anything else."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    def read(self) -> Any:
        value = self._value(0)
        if self._peek():
            raise ValueError(f"unexpected text at offset {self.pos}")
        return value

    def _peek(self) -> str:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _take(self, expected: str) -> None:
        if self._peek() != expected:
            raise ValueError(f"expected {expected!r} at offset {self.pos}")
        self.pos += 1

    def _value(self, depth: int) -> Any:
        if depth > _GV_MAX_DEPTH:
            raise ValueError("value nested too deeply")
        c = self._peek()
        if c == "(":
            return tuple(self._items("(", ")", depth))
        if c == "[":
            return self._items("[", "]", depth)
        if c == "{":
            return self._dict(depth)
        if c == "<":
            self.pos += 1
            inner = self._value(depth + 1)
            self._take(">")
            return inner
        if c in ("'", '"'):
            return _gv_unescape(self._quoted())
        if c == "b" and self.text[self.pos + 1:self.pos + 2] in ("'", '"'):
            self.pos += 1
            return _gv_unescape_bytes(self._quoted())
        if c == "@":
            self.pos += 1
            self._skip_type(depth)
            return self._value(depth + 1)
        number = _GV_NUMBER.match(self.text, self.pos)
        if number:
            self.pos = number.end()
            token = number.group(0)
            digits = token.lstrip("+-")
            if digits[:2] in ("0x", "0X"):
                return int(token, 16)
            if digits in ("inf", "nan") or any(ch in digits for ch in ".eE"):
                return float(token)
            return int(token)
        word = _GV_WORD.match(self.text, self.pos)
        if word:
            self.pos = word.end()
            name = word.group(0)
            if name in ("true", "false"):
                return name == "true"
            if name == "nothing":
                return None
            if name == "just" or name in _GV_TYPE_WORDS:
                return self._value(depth + 1)
            raise ValueError(f"unknown word {name!r} at offset {word.start()}")
        raise ValueError(f"unexpected {c!r} at offset {self.pos}" if c else "text ends early")

    def _quoted(self) -> str:
        quote = self.text[self.pos]
        end = self.pos + 1
        while end < len(self.text) and self.text[end] != quote:
            end += 2 if self.text[end] == "\\" else 1
        if end >= len(self.text):
            raise ValueError("unterminated string")
        raw = self.text[self.pos + 1:end]
        self.pos = end + 1
        return raw

    def _items(self, open_: str, close: str, depth: int) -> List[Any]:
        self._take(open_)
        items: List[Any] = []
        if self._peek() == close:
            self.pos += 1
            return items
        while True:
            items.append(self._value(depth + 1))
            c = self._peek()
            if c == close:
                self.pos += 1
                return items
            self._take(",")
            if self._peek() == close:  # a one-element tuple prints as "(x,)"
                self.pos += 1
                return items

    def _dict(self, depth: int) -> Dict[Any, Any]:
        self._take("{")
        result: Dict[Any, Any] = {}
        if self._peek() == "}":
            self.pos += 1
            return result
        while True:
            key = self._value(depth + 1)
            separator = self._peek()
            if separator not in (":", ","):  # "{k, v}" is a lone dictionary entry
                raise ValueError(f"expected ':' at offset {self.pos}")
            self.pos += 1
            value = self._value(depth + 1)
            try:
                result[key] = value
            except TypeError:
                raise ValueError("dictionary key is not a basic value") from None
            if self._peek() == "}":
                self.pos += 1
                return result
            if separator == ",":
                raise ValueError(f"expected '}}' at offset {self.pos}")
            self._take(",")

    def _skip_type(self, depth: int) -> None:
        """Step over a type string such as 'as', 'a{sv}' or 'm(is)'."""
        if depth > _GV_MAX_DEPTH:
            raise ValueError("type nested too deeply")
        c = self.text[self.pos:self.pos + 1]
        self.pos += 1
        if c and c in _GV_BASIC_TYPES:
            return
        if c in ("a", "m"):
            self._skip_type(depth + 1)
            return
        if c == "(":
            while self.text[self.pos:self.pos + 1] != ")":
                if self.pos >= len(self.text):
                    raise ValueError("type string ends early")
                self._skip_type(depth + 1)
            self.pos += 1
            return
        if c == "{":
            self._skip_type(depth + 1)
            self._skip_type(depth + 1)
            if self.text[self.pos:self.pos + 1] != "}":
                raise ValueError("bad dictionary entry type")
            self.pos += 1
            return
        raise ValueError(f"bad type character {c!r}" if c else "type string ends early")


def parse_gvariant(text: str) -> Any:
    """Parse GVariant text as `gdbus call` prints it. Raises ValueError."""
    return _GVariantReader(text.strip()).read()


def _reply_value(text: str) -> Any:
    """The one value of a gdbus reply: "(<'Playing'>,)" gives 'Playing'."""
    value = parse_gvariant(text)
    if isinstance(value, tuple) and len(value) == 1:
        return value[0]
    return value


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _count(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return int(min(value, 2 ** 63))


# ===== Volume levels =====

_VOLUME_LEVEL = re.compile(r"([+-]?)\s*([0-9]+(?:\.[0-9]*)?|\.[0-9]+)\s*%?")


def normalize_volume_level(raw: Any) -> Tuple[str, str]:
    """Turn a requested volume into amixer's terms: ('50', ''), ('+10', ''),
    ('mute', ''). A percentage sign is accepted and decimals are rounded. Levels
    and steps above 100 become 100, and the second item then says so. Only text
    with a leading '+' or '-' is a step; a number is always an absolute level.
    Raises ValueError with a message for the caller."""
    if isinstance(raw, bool) or (isinstance(raw, (int, float)) and not raw >= 0):
        raise ValueError(f"level {raw!r} is not a percentage 0-100; for a step send text such as '-10'")
    text = str(raw).strip()
    if text.lower() in ("mute", "unmute"):
        return text.lower(), ""
    match = _VOLUME_LEVEL.fullmatch(text)
    if not match:
        raise ValueError(f"level '{text}' is not a percentage such as '50' or '50%', a step such as "
                         "'+10' or '-10', 'mute' or 'unmute'")
    sign, number = match.groups()
    percent = float(number)
    level = 100 if percent > 100 else int(percent + 0.5)
    note = f"{sign}{number}% is more than 100, so {sign}100% was used" if percent > 100 else ""
    return f"{sign}{level}", note


# ===== The VLC Guaardvark started =====

def _vlc_pidfile() -> Path:
    """Where the VLC that Guaardvark started is recorded as "<pid> <starttime>",
    shared by the chat and MCP processes so either can replace it without touching
    other players. It lives in the git-ignored cache folder."""
    try:
        from backend.config import CACHE_DIR
        base = Path(CACHE_DIR)
    except Exception:
        base = Path(__file__).resolve().parents[2] / "data" / "cache"
    return base / "media" / "vlc.pid"


@contextmanager
def _vlc_launch_lock():
    """Serialise replacing and launching VLC across threads and processes, so two
    plays at once (chat and MCP) cannot both start a VLC and record only one."""
    lock_path = _vlc_pidfile().with_name("vlc.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a")
        fcntl.flock(handle, fcntl.LOCK_EX)
    except OSError as e:
        logger.debug(f"Could not take the VLC launch lock: {e}")
    try:
        yield
    finally:
        if handle:
            handle.close()


def _proc_stat(pid: int) -> Optional[Tuple[str, str]]:
    """(state, starttime) of a process, from fields 3 and 22 of /proc/<pid>/stat,
    or None when there is no such process. Field 2 (the command name) may contain
    spaces and ')', so the fields are counted from after the last ')'."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat[stat.rfind(")") + 1:].split()
    if len(fields) < 20:
        return None
    return fields[0], fields[19]


def _proc_is_vlc(pid: int) -> bool:
    """True when the process's executable or argv[0] is named exactly 'vlc'."""
    names = []
    try:
        names.append(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)"))
    except OSError:
        pass
    try:
        argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0]
        names.append(argv0.decode(errors="replace"))
    except OSError:
        pass
    return any(os.path.basename(name) == "vlc" for name in names)


def _proc_gone(pid: int, started: str) -> bool:
    """True when the process recorded with this start time has exited (a zombie
    counts as exited) or its PID now belongs to another process."""
    stat = _proc_stat(pid)
    return stat is None or stat[1] != started or stat[0] in ("Z", "X")


def _signal_process(pid: int, sig: int) -> None:
    os.kill(pid, sig)


def _wait_gone(pid: int, started: str, seconds: float) -> bool:
    """Wait up to `seconds` for the recorded process to exit; True once it has."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.waitpid(pid, os.WNOHANG)  # reaps it when this process started it
        except (OSError, OverflowError):
            pass
        if _proc_gone(pid, started):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _read_vlc_record() -> Optional[Tuple[int, str]]:
    """(pid, starttime) from the pidfile. Anything else, such as a pid-only file
    from an older version or a PID of 0 or 1, counts as no record."""
    try:
        recorded = _vlc_pidfile().read_text().strip()
    except OSError:
        return None
    match = re.fullmatch(r"([0-9]{1,10}) ([0-9]{1,20})", recorded)
    if not match or int(match.group(1)) <= 1:
        return None
    return int(match.group(1)), match.group(2)


def _running_guaardvark_vlc() -> Optional[int]:
    """PID of the VLC Guaardvark started, while that same process still runs."""
    record = _read_vlc_record()
    if record is None or _proc_gone(*record):
        return None
    return record[0]


# ===== Folders media_play may open =====

# Top-level folders that hold the system rather than music. /run/media (removable
# drives) is allowed, and so is everything inside the home folder, which some
# systems keep under /var (/var/home).
_SYSTEM_TOP_FOLDERS = {"etc", "proc", "sys", "dev", "boot", "root", "run", "var", "usr", "bin", "sbin"}


def _folder_refusal(folder: Path) -> Optional[str]:
    """Why a resolved absolute folder may not be played, or None when it may."""
    home = Path.home().resolve()
    if home != Path("/") and (folder == home or home in folder.parents):
        inside = folder.relative_to(home).parts
    else:
        inside = folder.parts[1:]
        if not inside:
            return "is the root folder"
        top = inside[0]
        if (top in _SYSTEM_TOP_FOLDERS or top.startswith("lib")) and inside[:2] != ("run", "media"):
            return f"is in a system folder (/{top})"
    hidden = next((part for part in inside if part.startswith(".")), None)
    if hidden:
        return f"is in a hidden folder ({hidden})"
    return None


class MediaPlayerService:
    """Singleton service for media player control via gdbus + MPRIS2 and VLC."""

    _instance: Optional["MediaPlayerService"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    @classmethod
    def get_instance(cls) -> "MediaPlayerService":
        return cls()

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        logger.info("MediaPlayerService initialized")

    def _get_music_directory(self) -> str:
        """Read music directory from DB settings, fall back to ~/Music. The MCP
        server has no app database, so there it asks the backend."""
        from backend.utils.backend_http import in_mcp_process
        if in_mcp_process():
            try:
                from backend.utils.backend_http import request_json
                value = ((request_json("GET", "/api/settings/music_directory").data or {})
                         .get("music_directory") or "").strip()
                if value:
                    return value
            except Exception as e:
                logger.debug(f"Could not read music_directory from the backend: {e}")
        else:
            try:
                from backend.models import db, Setting
                setting = db.session.get(Setting, "music_directory")
                if setting and setting.value and setting.value.strip():
                    return setting.value.strip()
            except Exception as e:
                logger.debug(f"Could not read music_directory setting: {e}")
        return str(Path.home() / "Music")

    # ===== gdbus =====

    def _gdbus(self, dest: str, object_path: str, method: str, *args: str) -> str:
        """Run one `gdbus call` on the session bus and return what it printed.
        Raises RuntimeError with a message fit for the user."""
        try:
            result = subprocess.run(
                ["gdbus", "call", "--session", "--dest", dest,
                 "--object-path", object_path, "--method", method, *args],
                capture_output=True, text=True, timeout=5,
            )
        except FileNotFoundError:
            raise RuntimeError("gdbus is not installed (it comes with GLib, e.g. the libglib2.0-bin "
                               "package), so media players cannot be reached over D-Bus.") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{dest} did not answer {method.rsplit('.', 1)[-1]} within 5 s.") from None
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"gdbus {method} failed")
        return result.stdout.strip()

    def _gdbus_get_property(self, bus_name: str, iface: str, prop: str) -> str:
        """A D-Bus property as gdbus prints it, e.g. "(<'Playing'>,)"."""
        return self._gdbus(bus_name, MPRIS2_OBJECT_PATH, f"{MPRIS2_PROPS_IFACE}.Get", iface, prop)

    def _mpris_names(self) -> List[str]:
        """Every MPRIS2 player's bus name, sorted so listings are stable."""
        raw = self._gdbus(DBUS_NAME, DBUS_PATH, f"{DBUS_NAME}.ListNames")
        try:
            names = _reply_value(raw)
        except ValueError as e:
            raise RuntimeError(f"Could not read the D-Bus name list: {e}") from None
        if not isinstance(names, list):
            return []
        return sorted(n for n in names if isinstance(n, str) and n.startswith(MPRIS2_BUS_PREFIX))

    def _bus_owner_pid(self, bus_name: str) -> Optional[int]:
        try:
            pid = _reply_value(self._gdbus(DBUS_NAME, DBUS_PATH,
                                           f"{DBUS_NAME}.GetConnectionUnixProcessID", bus_name))
        except (RuntimeError, ValueError):
            return None
        return pid if isinstance(pid, int) and not isinstance(pid, bool) else None

    def _playback_status(self, bus_name: str) -> Optional[str]:
        """'Playing', 'Paused' or 'Stopped', or None when the player does not say."""
        try:
            status = _reply_value(self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "PlaybackStatus"))
        except (RuntimeError, ValueError):
            return None
        return status if isinstance(status, str) and status else None

    def _player_display_name(self, bus_name: str) -> str:
        if bus_name.startswith(MPRIS2_BUS_PREFIX):
            return bus_name[len(MPRIS2_BUS_PREFIX):]
        return bus_name

    # ===== Choosing a player =====

    def _guaardvark_vlc_bus(self, names: List[str]) -> Tuple[Optional[int], Optional[str]]:
        """(pid, bus name) of the VLC Guaardvark started, while it runs; the bus
        name is None until that VLC has registered on D-Bus. VLC takes
        org.mpris.MediaPlayer2.vlc when it is free and ...vlc.instance<pid> when
        another VLC holds it, so only those two names can be it."""
        pid = _running_guaardvark_vlc()
        if pid is None:
            return None, None
        instance = f"{MPRIS2_BUS_PREFIX}vlc.instance{pid}"
        if instance in names:
            return pid, instance
        plain = MPRIS2_BUS_PREFIX + "vlc"
        if plain in names and self._bus_owner_pid(plain) == pid:
            return pid, plain
        return pid, None

    def _named_player(self, wanted: str, names: List[str]) -> str:
        """The bus name for a player the caller named. Takes the short name
        ('spotify') or the full bus name, matches case-insensitively when that is
        unambiguous, and lets 'vlc' mean a lone 'vlc.instance<pid>'."""
        short = wanted[len(MPRIS2_BUS_PREFIX):] if wanted.startswith(MPRIS2_BUS_PREFIX) else wanted
        if not _PLAYER_NAME.fullmatch(short):
            raise RuntimeError(f"'{wanted}' is not a player name; give the part of its D-Bus name after "
                               f"'{MPRIS2_BUS_PREFIX}', e.g. 'vlc' or 'spotify'.")
        if not names:
            raise RuntimeError(NO_PLAYER)
        exact = MPRIS2_BUS_PREFIX + short
        if exact in names:
            return exact
        lowered = short.lower()
        shorts = {n: self._player_display_name(n).lower() for n in names}
        for candidates in ([n for n in names if shorts[n] == lowered],
                           [n for n in names if shorts[n].startswith(lowered + ".")]):
            if len(candidates) == 1:
                return candidates[0]
        running = ", ".join(self._player_display_name(n) for n in names)
        raise RuntimeError(f"No player named '{short}' is running. Open players: {running}.")

    def _choose_player(self, player_name: Optional[str] = None) -> Tuple[str, str, List[str]]:
        """(bus name, why it was chosen, every MPRIS2 bus name). With a player
        named, that player and an empty reason. Otherwise, in order: the VLC
        Guaardvark started, the only player open, the only one playing. When
        none of those applies nothing is chosen and the RuntimeError lists the
        players, so the caller can name one instead of the command landing on
        whichever player D-Bus happens to list first."""
        names = self._mpris_names()
        wanted = str(player_name or "").strip()
        if wanted:
            return self._named_player(wanted, names), "", names

        pid, ours = self._guaardvark_vlc_bus(names)
        if ours:
            return ours, CHOSEN_GUAARDVARK, names
        if pid is not None:
            others = ", ".join(self._player_display_name(n) for n in names)
            raise RuntimeError(
                f"The VLC Guaardvark started (pid {pid}) is not on D-Bus yet, so it cannot be reached; "
                "try again in a moment"
                + (f", or name one of the other players with player: {others}" if others else "") + ".")
        if not names:
            raise RuntimeError(NO_PLAYER)
        if len(names) == 1:
            return names[0], CHOSEN_ONLY, names

        statuses = {n: self._playback_status(n) for n in names}
        playing = [n for n in names if statuses[n] == "Playing"]
        if len(playing) == 1:
            return playing[0], CHOSEN_PLAYING, names
        players = [(self._player_display_name(n), statuses[n] or "status unknown") for n in names]
        listing = ", ".join(f"{name} ({status})" for name, status in players)
        raise PlayerChoiceNeeded(
            f"{len(names)} media players are open, none started by Guaardvark, and "
            + ("none is playing" if not playing else f"{len(playing)} are playing")
            + f": {listing}. Name the one to use with player, "
            f"e.g. player='{self._player_display_name((playing or names)[0])}'.",
            players)

    # ===== MPRIS2 Methods =====

    def list_players(self) -> Dict[str, Any]:
        """List all running MPRIS2 media players."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            players = [self._player_display_name(n) for n in self._mpris_names()]
            return {"success": True, "players": players, "count": len(players)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _call_player_method(self, method: str, player_name: Optional[str] = None) -> Dict[str, Any]:
        """Call a method on the MPRIS2 Player interface of the chosen player."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name, why, _names = self._choose_player(player_name)
            self._gdbus(bus_name, MPRIS2_OBJECT_PATH, f"{MPRIS2_PLAYER_IFACE}.{method}")
            return {
                "success": True,
                "player": self._player_display_name(bus_name),
                "bus_name": bus_name,
                "chosen_because": why,
                "action": method.lower(),
            }
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"MPRIS2 {method} failed: {e}"}

    def play(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Play", player_name)

    def pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Pause", player_name)

    def play_pause(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("PlayPause", player_name)

    def stop(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Stop", player_name)

    def next_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Next", player_name)

    def previous_track(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        return self._call_player_method("Previous", player_name)

    def get_status(self, player_name: Optional[str] = None) -> Dict[str, Any]:
        """Playback status and track metadata of the chosen player."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            bus_name, why, names = self._choose_player(player_name)
            status = self._playback_status(bus_name) or "Unknown"
            track_info = self._parse_metadata(
                self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "Metadata"))
            try:
                volume = _reply_value(self._gdbus_get_property(bus_name, MPRIS2_PLAYER_IFACE, "Volume"))
                if isinstance(volume, (int, float)) and not isinstance(volume, bool) and volume == volume:
                    track_info["player_volume"] = round(volume * 100)
            except (RuntimeError, ValueError, OverflowError):
                pass  # Volume is optional in MPRIS2

            return {
                "success": True,
                "player": self._player_display_name(bus_name),
                "bus_name": bus_name,
                "chosen_because": why,
                "other_players": [self._player_display_name(n) for n in names if n != bus_name],
                "status": status,
                "track": track_info,
            }
        except PlayerChoiceNeeded as e:
            return {"success": False, "error": str(e), "choice_needed": True,
                    "players": [{"player": name, "status": status} for name, status in e.players]}
        except RuntimeError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": f"Failed to get status: {e}"}

    def _parse_metadata(self, raw: str) -> Dict[str, Any]:
        """Track fields from a Metadata reply, "(<{'xesam:title': <'...'>, ...}>,)".
        Unreadable text gives the 'Unknown' defaults rather than a guess."""
        info: Dict[str, Any] = {"title": "Unknown", "artist": "Unknown", "album": "Unknown"}
        try:
            meta = _reply_value(raw)
        except ValueError as e:
            logger.debug(f"Could not parse player metadata: {e}")
            meta = None
        if not isinstance(meta, dict):
            return info

        title = _text(meta.get("xesam:title"))
        if title:
            info["title"] = title

        # A list of names in MPRIS2; a few players send one string.
        artists = meta.get("xesam:artist")
        artists = [artists] if isinstance(artists, str) else artists
        if isinstance(artists, list):
            names = [a for a in (_text(x) for x in artists) if a]
            if names:
                info["artist"] = ", ".join(names)

        album = _text(meta.get("xesam:album"))
        if album:
            info["album"] = album

        length = _count(meta.get("mpris:length"))  # microseconds
        if length is not None:
            info["length_seconds"] = length // 1_000_000

        art = meta.get("mpris:artUrl")
        if isinstance(art, str) and art:
            info["art_url"] = art

        # With no title tag, or an empty one, the file name without its
        # extension is the next best thing.
        url = meta.get("xesam:url")
        if not title and isinstance(url, str) and url:
            from urllib.parse import unquote, urlparse
            name = Path(unquote(urlparse(url).path)).stem.strip()
            if name:
                info["title"] = name

        return info

    # ===== Volume Control =====

    def _amixer(self, *args: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["amixer", *args], capture_output=True, text=True, timeout=5)
        except FileNotFoundError:
            raise RuntimeError("amixer is not installed (it comes with alsa-utils), so the system "
                               "volume cannot be read or changed.") from None

    def get_volume(self) -> Dict[str, Any]:
        """Get current system volume via amixer."""
        try:
            result = self._amixer("get", "Master")
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}
            match = re.search(r"\[([0-9]+)%\]", result.stdout)
            level = int(match.group(1)) if match else None
            mute_match = re.search(r"\[(on|off)\]", result.stdout)
            muted = mute_match.group(1) == "off" if mute_match else False
            return {"success": True, "volume": level, "muted": muted}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def set_volume(self, level: Any) -> Dict[str, Any]:
        """Set system volume: '50', '50%', '+10', '-10', 'mute', 'unmute'.
        See normalize_volume_level for what else is accepted."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}
        try:
            level, note = normalize_volume_level(level)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        try:
            if level in ("mute", "unmute"):
                args = ("set", "Master", level)
            elif level[0] in "+-":
                args = ("set", "Master", f"{level[1:]}%{level[0]}")
            else:
                args = ("set", "Master", f"{level}%")

            result = self._amixer(*args)
            if result.returncode != 0:
                return {"success": False, "error": result.stderr.strip()}

            current = self.get_volume()
            return {
                "success": True,
                "volume": current.get("volume"),
                "muted": current.get("muted", False),
                "action": f"set volume to {level}",
                "note": note,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ===== Music File Search =====

    def find_music_files(self, query: str, search_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
        """Search for music files matching a query."""
        if not search_dirs:
            search_dirs = [self._get_music_directory()]

        query_lower = query.lower()
        query_parts = query_lower.split()
        matches = []

        for search_dir in search_dirs:
            search_path = Path(search_dir).expanduser()
            if not search_path.is_dir():
                continue
            try:
                for root, dirs, files in os.walk(search_path):
                    for filename in files:
                        ext = Path(filename).suffix.lower()
                        if ext not in MUSIC_EXTENSIONS:
                            continue
                        search_text = (filename + " " + Path(root).name).lower()
                        if all(part in search_text for part in query_parts):
                            matches.append(os.path.join(root, filename))
                            if len(matches) >= 50:
                                break
                    if len(matches) >= 50:
                        break
            except PermissionError:
                continue

        matches.sort()
        return {
            "success": True,
            "files": matches,
            "count": len(matches),
            "query": query,
            "search_dirs": search_dirs,
        }

    # ===== VLC Launch =====

    def _kill_existing_vlc(self):
        """End the VLC that Guaardvark started last and wait for it to exit. The
        pidfile outlives VLC and reboots and PIDs are reused, so the process is
        signalled only while its start time matches the one recorded and its
        executable or argv[0] is vlc. Players the user started are left alone.
        Call with _vlc_launch_lock held."""
        record = _read_vlc_record()
        try:
            _vlc_pidfile().unlink()
        except OSError:
            pass
        if record is None:
            return
        pid, started = record

        # A VLC launched a moment ago may still be in its launcher (snap run, a
        # wrapper script) on the way to exec'ing vlc under the same PID.
        deadline = time.monotonic() + 2.0
        while not _proc_is_vlc(pid):
            if _proc_gone(pid, started) or time.monotonic() >= deadline:
                return
            time.sleep(0.05)
        if _proc_gone(pid, started):
            return
        try:
            _signal_process(pid, signal.SIGTERM)
        except OSError:
            return
        # Wait for it to exit so the new VLC does not start beside it.
        if _wait_gone(pid, started, 2.0):
            return
        # A VLC stuck on its audio output can ignore SIGTERM; left running it
        # would play over the new one.
        if _proc_gone(pid, started) or not _proc_is_vlc(pid):
            return
        logger.warning(f"VLC {pid} was still running 2 s after SIGTERM; sending SIGKILL")
        try:
            _signal_process(pid, signal.SIGKILL)
        except OSError:
            return
        if not _wait_gone(pid, started, 1.0):
            logger.warning(f"VLC {pid} was still running 1 s after SIGKILL")

    def _record_vlc(self, pid: int) -> None:
        stat = _proc_stat(pid)
        if not stat:
            return
        try:
            pidfile = _vlc_pidfile()
            pidfile.parent.mkdir(parents=True, exist_ok=True)
            pidfile.write_text(f"{pid} {stat[1]}")
        except OSError as e:
            logger.debug(f"Could not record the VLC pid: {e}")

    def launch_vlc(self, files: Optional[List[str]] = None, directory: Optional[str] = None,
                   shuffle: bool = False) -> Dict[str, Any]:
        """Start VLC on files or a folder, replacing the VLC Guaardvark started
        before. A directory must be an existing absolute folder, and not the root,
        a system or a hidden folder unless it is inside the Settings music folder."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        if directory:
            folder = Path(directory).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                return {"success": False, "error": f"'{directory}' is not an existing folder (give an absolute path)"}
            folder = folder.resolve()
            refusal = _folder_refusal(folder)
            if refusal:
                music = Path(self._get_music_directory()).expanduser().resolve()
                if folder != music and music not in folder.parents:
                    return {"success": False,
                            "error": f"'{directory}' {refusal}; name a folder of music instead"}
            directory = str(folder)

        vlc_cmd = VLC_PATH
        if not os.path.exists(vlc_cmd):
            vlc_cmd = "vlc"

        # --no-one-instance: with VLC's "Allow only one instance" preference on, a
        # new vlc hands its playlist to a VLC the user already has open and exits.
        # --dbus: VLC exposes MPRIS2 only in one-instance mode or with this option
        # (its "dbus" setting is off by default), and media_control and
        # media_status reach this VLC through MPRIS2.
        # --no-metadata-network-access: no album-art or metadata lookups online.
        cmd = [vlc_cmd, "--no-one-instance", "--dbus", "--no-metadata-network-access"]
        if shuffle:
            cmd.append("--random")

        if directory:
            cmd.append(directory)
        elif files:
            cmd.extend(files)
        else:
            return {"success": False, "error": "No files or directory specified"}

        with _vlc_launch_lock():
            # Replace the VLC Guaardvark started before, so playlists do not pile up.
            self._kill_existing_vlc()
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except FileNotFoundError:
                return {"success": False, "error": "VLC not found. Install VLC to play music."}
            except Exception as e:
                return {"success": False, "error": f"Failed to launch VLC: {e}"}
            self._record_vlc(proc.pid)

        file_count = len(files) if files else 0
        return {
            "success": True,
            "pid": proc.pid,
            "file_count": file_count,
            "directory": directory,
            "shuffle": shuffle,
            "action": "launched VLC",
        }

    # ===== High-Level Play =====

    def play_music(self, query: str = "", shuffle: bool = False) -> Dict[str, Any]:
        """Find music files matching query and play them in VLC.
        If query is empty or generic, plays all music in the music directory."""
        if not MEDIA_CONTROL_ENABLED:
            return {"success": False, "error": "Media control disabled"}

        music_dir = self._get_music_directory()

        # For empty/generic queries, play the entire music directory
        if not query or query.lower() in ("music", "some music", "my music", "all", "everything", "anything", "songs"):
            return self.launch_vlc(directory=music_dir, shuffle=shuffle or True)

        search_result = self.find_music_files(query)
        if not search_result["success"]:
            return search_result

        files = search_result["files"]
        if not files:
            return {
                "success": False,
                "error": f"No music files found matching '{query}' in {music_dir}. "
                         f"Check that your music directory is set correctly in Settings.",
            }

        launch_result = self.launch_vlc(files=files, shuffle=shuffle)
        if not launch_result["success"]:
            return launch_result

        return {
            "success": True,
            "action": "playing",
            "pid": launch_result.get("pid"),
            "query": query,
            "file_count": len(files),
            "shuffle": shuffle,
            "files": files[:10],
            "total_matches": len(files),
        }


def get_media_service() -> MediaPlayerService:
    return MediaPlayerService.get_instance()
