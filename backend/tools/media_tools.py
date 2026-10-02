#!/usr/bin/env python3
"""
Media Player Tools
Tools for controlling music playback, volume, and checking current track info.

Linux only (D-Bus MPRIS2, amixer, VLC). tool_registry_init registers them only
there; each tool also refuses on its own elsewhere.
"""

import logging
from typing import Any, Dict, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.media_player_service import (
    MEDIA_CONTROL_ENABLED,
    get_media_service,
    normalize_volume_level,
)
from backend.utils import platform as _platform

logger = logging.getLogger(__name__)

# How media_control and media_status pick a player when none is named, in the
# words both descriptions use.
_DEFAULT_PLAYER_RULE = (
    "Without player it picks, in order: the VLC Guaardvark started with media_play (while it is "
    "open), else the only player open, else the only one playing."
)

_PLAYER_PARAM = (
    "Player to {verb}: the part of its D-Bus name after 'org.mpris.MediaPlayer2.', as media_status's "
    "Player line shows it, e.g. 'spotify', 'vlc' or 'vlc.instance4242' (the full bus name also works; "
    "case does not matter, and 'vlc' also finds a lone 'vlc.instance<pid>'). Omit to use the default "
    "described above."
)


def _unavailable_result() -> Optional[ToolResult]:
    """A refusal when these tools cannot work here or are switched off, else None."""
    if not _platform.media_player_available():
        system = {"Darwin": "macOS"}.get(_platform.os_name(), _platform.os_name())
        return ToolResult(
            success=False,
            error=(f"The media tools work only on Linux: they reach players over D-Bus (MPRIS2), set "
                   f"volume with ALSA's amixer and play music in VLC. This machine runs {system}, so "
                   "nothing was played or changed."),
        )
    if not MEDIA_CONTROL_ENABLED:
        return ToolResult(
            success=False,
            error="Media control disabled. Set GUAARDVARK_MEDIA_CONTROL=true to enable."
        )
    return None


def _player_label(result: Dict[str, Any]) -> str:
    """'spotify', or 'vlc (the VLC Guaardvark started)' when it was the default."""
    player = result.get("player") or "unknown"
    why = result.get("chosen_because")
    return f"{player} ({why})" if why else player


class MediaPlayTool(BaseTool):
    """Play music by searching for songs/artists/albums, or resume playback."""

    name = "media_play"
    read_only = False
    destructive = False
    description = (
        "Play local music files in VLC on the machine running this Guaardvark server (Linux only). "
        "query searches the music folder set in Settings (~/Music by default) for audio files whose "
        "file name plus the name of the folder they are in contains every query word "
        "(case-insensitive; parent folders are not searched, so in an Artist/Album/track layout the "
        "artist name does not match) and plays up to 50 of them, returning e.g. \"playing 12 tracks "
        "matching 'jazz' in Guaardvark's VLC (pid 4242)\", or an error when none match. query "
        "'music' (also 'all', 'songs', 'everything') plays the whole music folder, always shuffled; "
        "directory plays a folder you name instead. Matches and directory play in order unless "
        "shuffle is true. Starting new music replaces the VLC that Guaardvark started before; "
        "players the user opened are left alone, and VLC is started with its one-instance mode off "
        "so it never hands the music to a VLC already open. While that VLC is open, media_control "
        "and media_status act on it when no player is named. With neither query nor directory it "
        "sends Play to the player media_control would pick, to resume. Pause or skip with "
        "media_control, set loudness with media_volume, see the current track with media_status."
    )
    parameters = {
        "query": ToolParameter(
            name="query", type="string", required=False,
            description="Words that must each appear in a file's name or the name of the folder it "
                        "is in, e.g. 'Alice in Chains' or 'jazz'. Use 'music' to play everything. "
                        "Omit, with no directory, to resume playback."
        ),
        "shuffle": ToolParameter(
            name="shuffle", type="bool", required=False,
            description="Play in random order (default false). query 'music' always shuffles.", default=False
        ),
        "directory": ToolParameter(
            name="directory", type="string", required=False,
            description="Absolute path of a folder to play in full, e.g. '~/Music/Live' or '/mnt/music/Live'; "
                        "overrides query. It must be an existing folder. Refused: '/', system folders "
                        "(/etc, /usr, /var, /proc, /sys, /dev, /boot, /root, /bin, /sbin, /lib*, and /run "
                        "except /run/media) and hidden folders such as ~/.ssh (any part starting with "
                        "'.'), unless inside the Settings music folder. Other folders, e.g. on /mnt or "
                        "/media, are allowed."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        refusal = _unavailable_result()
        if refusal:
            return refusal

        service = get_media_service()
        # str(): parameters may arrive as non-strings (e.g. a song called "1999")
        query = str(kwargs.get("query") or "").strip()
        shuffle = kwargs.get("shuffle", False)
        directory = str(kwargs.get("directory") or "").strip()

        if directory:
            result = service.launch_vlc(directory=directory, shuffle=shuffle)
        elif query:
            result = service.play_music(query, shuffle=shuffle)
        else:
            result = service.play()

        if not result.get("success"):
            return ToolResult(success=False, error=result.get("error", "Unknown error"))

        if not result.get("pid"):
            # Resumed a player that was already open.
            return ToolResult(success=True, output=f"Playing on {_player_label(result)}", metadata=result)

        output = "playing"
        if result.get("file_count"):
            output += f" {result['file_count']} tracks"
        if result.get("query"):
            output += f" matching '{result['query']}'"
        if result.get("directory"):
            output += f" from {result['directory']}"
        if result.get("shuffle"):
            output += ", shuffled,"
        output += (f" in Guaardvark's VLC (pid {result['pid']}); media_control and media_status act "
                   "on it when no player is named")
        return ToolResult(success=True, output=output, metadata=result)


class MediaControlTool(BaseTool):
    """Control media playback: pause, stop, next, previous, toggle."""

    name = "media_control"
    read_only = False
    destructive = False
    description = (
        "Send play, pause, stop, next, previous or toggle to a media player already open on the "
        "machine running this Guaardvark server (Linux only), over MPRIS2 on the D-Bus session bus. "
        "Works on any MPRIS2 player, not only the VLC that media_play starts. "
        + _DEFAULT_PLAYER_RULE +
        " When several players are open and none of those applies it acts on none and returns an "
        "error listing them with their status, so you can name one."
        " Returns the player it acted on, and why when it picked one, e.g. 'Paused on vlc (the VLC "
        "Guaardvark started)' or 'Skipped to next track on spotify', or an error when no player is "
        "running. play, pause and stop are safe to repeat; next, previous and toggle act again on "
        "every call. Start music with media_play, change loudness with media_volume, read the "
        "current track with media_status."
    )
    parameters = {
        "action": ToolParameter(
            name="action", type="string", required=True,
            enum=["play", "pause", "stop", "next", "previous", "toggle"],
            description="play resumes a paused player; pause; stop; next and previous change track; "
                        "toggle switches between playing and paused."
        ),
        "player": ToolParameter(
            name="player", type="string", required=False,
            description=_PLAYER_PARAM.format(verb="control"),
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        refusal = _unavailable_result()
        if refusal:
            return refusal

        service = get_media_service()
        action = str(kwargs.get("action") or "").strip().lower()
        player = str(kwargs.get("player") or "").strip() or None

        action_map = {
            "play": (service.play, "Playing"),
            "resume": (service.play, "Playing"),
            "pause": (service.pause, "Paused"),
            "stop": (service.stop, "Stopped"),
            "next": (service.next_track, "Skipped to next track"),
            "previous": (service.previous_track, "Went to previous track"),
            "prev": (service.previous_track, "Went to previous track"),
            "toggle": (service.play_pause, "Toggled playback"),
        }

        if action not in action_map:
            return ToolResult(
                success=False,
                error=f"Unknown action '{action}'. Use: play, pause, stop, next, previous, toggle"
            )

        func, label = action_map[action]
        result = func(player)
        if result.get("success"):
            return ToolResult(success=True, output=f"{label} on {_player_label(result)}", metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error", "Unknown error"))


class MediaVolumeTool(BaseTool):
    """Get or set the system audio volume."""

    name = "media_volume"
    read_only = False
    destructive = False
    description = (
        "Read or change the system output volume of the machine running this Guaardvark server "
        "(Linux only): the ALSA 'Master' control via amixer, which affects every application, not "
        "only the player. Omit level to read it without changing anything (returns 'Volume: 30%', "
        "with ' (muted)' when muted). Set it with level as text: '50' or '50%' (percent), '+10' or "
        "'-10' (points up or down), 'mute' or 'unmute'; decimals are rounded, and a level or step "
        "above 100 is used as 100 and the result says so. Returns 'Volume set to N%'. Absolute, mute "
        "and unmute give the same result on repeat; relative steps add up. Use media_control to "
        "pause or skip, media_status for the current track."
    )
    parameters = {
        "level": ToolParameter(
            name="level", type="string", required=False,
            description="Text, e.g. '50' or '50%' (percent, above 100 is used as 100), '+10' or '-10' "
                        "(step), 'mute' or 'unmute'; omit to read the volume. Over MCP a number such "
                        "as 50 fails schema validation; send '50'."
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        refusal = _unavailable_result()
        if refusal:
            return refusal

        service = get_media_service()
        # MCP sends text only (the schema refuses numbers); chat's reflex passes an
        # int for "volume 50", which is always an absolute level. Only text with an
        # explicit leading '+' or '-' is a step.
        raw_level = kwargs.get("level")
        if raw_level is None or (isinstance(raw_level, str) and not raw_level.strip()):
            result = service.get_volume()
            if result.get("success"):
                vol = result.get("volume")
                muted = result.get("muted", False)
                status = f"Volume: {vol}%" + (" (muted)" if muted else "")
                return ToolResult(success=True, output=status, metadata=result)
            else:
                return ToolResult(success=False, error=result.get("error"))

        try:
            level, note = normalize_volume_level(raw_level)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        result = service.set_volume(level)
        if result.get("success"):
            vol = result.get("volume")
            muted = result.get("muted", False)
            output = f"Volume set to {vol}%" + (" (muted)" if muted else "")
            if note:
                output += f" ({note})"
            return ToolResult(success=True, output=output, metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))


class MediaStatusTool(BaseTool):
    """Get current playback status and track info."""

    name = "media_status"
    read_only = True
    # A status poll: repeating it changes nothing.
    idempotent = True
    description = (
        "Report what a media player on the machine running this Guaardvark server is playing "
        "(Linux only), read over MPRIS2 (D-Bus) without changing anything. "
        + _DEFAULT_PLAYER_RULE +
        " When several are open and none of those applies it lists them with their status and asks "
        "you to name one. Otherwise it returns text lines: Player (the name to pass as player), "
        "Chosen (why, when no player was "
        "named), Status (Playing, Paused or Stopped), Title (the file name without extension when "
        "the track has no title tag or an empty one), Artist, Album ('Unknown' when the player "
        "gives none), Length m:ss, the player's own volume when known, and Other players when more "
        "are open. When no player is running it says 'No media player is running.' Start music with "
        "media_play; for system volume use media_volume; to pause or skip use media_control."
    )
    parameters = {
        "player": ToolParameter(
            name="player", type="string", required=False,
            description=_PLAYER_PARAM.format(verb="read"),
        ),
    }

    def execute(self, **kwargs) -> ToolResult:
        refusal = _unavailable_result()
        if refusal:
            return refusal

        service = get_media_service()
        player = str(kwargs.get("player") or "").strip() or None

        result = service.get_status(player)
        if not result.get("success") and "No media player" in (result.get("error") or ""):
            # Nothing playing is an answer, not a failure.
            return ToolResult(success=True, output="No media player is running.", metadata=result)
        if result.get("choice_needed"):
            # So is the list of open players when none of them is the obvious one.
            return ToolResult(success=True, output=result["error"], metadata=result)
        if result.get("success"):
            track = result.get("track", {})
            status = result.get("status", "Unknown")
            lines = [f"Player: {result.get('player', 'unknown')}"]
            if result.get("chosen_because"):
                lines.append(f"Chosen: {result['chosen_because']}")
            lines += [
                f"Status: {status}",
                f"Title: {track.get('title', 'Unknown')}",
                f"Artist: {track.get('artist', 'Unknown')}",
                f"Album: {track.get('album', 'Unknown')}",
            ]
            if track.get("length_seconds"):
                mins, secs = divmod(track["length_seconds"], 60)
                lines.append(f"Length: {mins}:{secs:02d}")
            if track.get("player_volume") is not None:
                lines.append(f"Player volume: {track['player_volume']}%")
            if result.get("other_players"):
                lines.append(f"Other players: {', '.join(result['other_players'])}")
            return ToolResult(success=True, output="\n".join(lines), metadata=result)
        else:
            return ToolResult(success=False, error=result.get("error"))
