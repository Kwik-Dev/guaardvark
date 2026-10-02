"""Media player tools: metadata parsing, which player a command lands on, the
Linux-only gate, and replacing the VLC Guaardvark started.

Nothing here reaches a real player or starts VLC: the service's subprocess is
replaced by a fake session bus, signals are recorded instead of sent, and the
process table is faked with PIDs above the kernel's pid_max.
"""
from __future__ import annotations

import signal
import threading
import time
from types import SimpleNamespace

import pytest

from backend.services import media_player_service as mps
from backend.tools import media_tools
from backend.utils import platform as plat

SPOTIFY = "org.mpris.MediaPlayer2.spotify"
VLC = "org.mpris.MediaPlayer2.vlc"
OURS_PID, USER_VLC_PID = 2_100_000_001, 2_100_000_002
OURS_INSTANCE = f"org.mpris.MediaPlayer2.vlc.instance{OURS_PID}"

# Metadata replies as `gdbus call` prints them (made with GLib's g_variant_print).
URL = "'xesam:url': <'file:///home/me/Music/Real%20Name.mp3'>"
NO_TITLE_FAKE_IN_ALBUM = (
    "(<{'mpris:trackid': <objectpath '/org/videolan/vlc/playlist/3'>, " + URL + ", "
    "'xesam:artist': <['x']>, 'xesam:album': <\"'xesam:title': <'FAKE TITLE'>\">, "
    "'mpris:length': <int64 240600860>}>,)")
NO_TITLE_FAKE_IN_COMMENT = (
    "(<{" + URL + ", 'xesam:album': <'real album'>, "
    "'xesam:comment': <[\"'xesam:title': <'FROM COMMENT'>\"]>}>,)")
TITLE_HOLDS_FAKE_ALBUM = "(<{" + URL + ", 'xesam:title': <\"'xesam:album': <'FAKE ALBUM'>\">}>,)"
ALBUM_BEFORE_REAL_TITLE = (
    "(<{'xesam:album': <\"has 'xesam:title': <'FAKE'> in it\">, 'xesam:title': <'Real Title'>}>,)")
ESCAPES = (
    "(<{'xesam:title': <\"Don't say \\\"hi\\\"\">, 'xesam:artist': <[\"Guns N' Roses\", 'a]>b']>, "
    "'xesam:album': <'line1\\nline2\\tend \\u001b \\\\x'>, 'mpris:artUrl': <\"file:///a/it's.jpg\">, "
    "'x:bytes': <b\"it's\\001\\303\\251\\n\\\\\">, 'x:maybe': <@mv nothing>, 'x:empty': <@as []>, "
    "'x:dict': <@a{sv} {}>, 'x:inf': <-inf>, 'x:byte': <byte 0x03>, 'x:tuple': <(1, 'a')>, "
    "'x:u64': <uint64 18446744073709551615>}>,)")


# ---- fixtures -----------------------------------------------------------------------------
class FakeBus:
    """players: {bus name: {"status": ..., "pid": ..., "meta": ...}}; `order` is
    the order ListNames reports them in."""

    def __init__(self, players, order=None):
        self.players = players
        self.order = order or list(players)
        self.calls = []

    def run(self, cmd, **kwargs):
        assert cmd[:3] == ["gdbus", "call", "--session"], cmd
        dest, method = cmd[cmd.index("--dest") + 1], cmd[cmd.index("--method") + 1]
        args = cmd[cmd.index("--method") + 2:]
        self.calls.append((dest, method.rsplit(".", 1)[-1]))
        ok = lambda out: SimpleNamespace(returncode=0, stdout=out + "\n", stderr="")
        if dest == "org.freedesktop.DBus":
            if method.endswith("ListNames"):
                names = [":1.7", "org.freedesktop.DBus"] + self.order
                return ok("([" + ", ".join(f"'{n}'" for n in names) + "],)")
            player = self.players.get(args[0])
            return ok(f"(uint32 {player['pid']},)") if player else \
                SimpleNamespace(returncode=1, stdout="", stderr="NameHasNoOwner")
        player = self.players[dest]
        if method.endswith("Properties.Get"):
            return ok({"PlaybackStatus": f"(<'{player['status']}'>,)",
                       "Metadata": player.get("meta", "(<@a{sv} {}>,)"),
                       "Volume": "(<0.5>,)"}[args[1]])
        return ok("()")

    def commands(self):
        return [(d, m) for d, m in self.calls if m in ("Play", "Pause", "PlayPause", "Stop", "Next", "Previous")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Linux, media control on, a private pidfile, a fake process table and
    recorded signals. Any subprocess call not faked by a test fails it."""
    monkeypatch.setattr(plat, "os_name", lambda: "Linux")
    monkeypatch.setattr(mps, "MEDIA_CONTROL_ENABLED", True)
    monkeypatch.setattr(media_tools, "MEDIA_CONTROL_ENABLED", True)
    pidfile = tmp_path / "cache" / "media" / "vlc.pid"
    monkeypatch.setattr(mps, "_vlc_pidfile", lambda: pidfile)
    procs = {}
    monkeypatch.setattr(mps, "_proc_stat", lambda pid: (procs[pid]["state"], procs[pid]["start"]) if pid in procs else None)
    monkeypatch.setattr(mps, "_proc_is_vlc", lambda pid: pid in procs and procs[pid]["vlc"])
    signals = []
    exits_on = set()

    def record_signal(pid, sig):
        signals.append((pid, signal.Signals(sig).name))
        if signal.Signals(sig).name in exits_on:
            procs.pop(pid, None)

    monkeypatch.setattr(mps, "_signal_process", record_signal)

    def refuse(*args, **kwargs):
        raise AssertionError(f"unexpected subprocess call {args}")

    monkeypatch.setattr(mps, "subprocess", SimpleNamespace(run=refuse, Popen=refuse, DEVNULL=-3,
                                                            TimeoutExpired=TimeoutError))
    music = tmp_path / "Music"
    music.mkdir()
    svc = mps.get_media_service()
    monkeypatch.setattr(svc, "_get_music_directory", lambda: str(music))
    return SimpleNamespace(pidfile=pidfile, procs=procs, signals=signals, exits_on=exits_on,
                           svc=svc, music=music, monkeypatch=monkeypatch)


def use_bus(env, players, order=None):
    bus = FakeBus(players, order)
    env.monkeypatch.setattr(mps.subprocess, "run", bus.run)
    return bus


def ours_running(env, start="111", alive=True):
    env.pidfile.parent.mkdir(parents=True, exist_ok=True)
    env.pidfile.write_text(f"{OURS_PID} 111")
    if alive:
        env.procs[OURS_PID] = {"state": "S", "start": start, "vlc": True}


def player(status, pid=1, **extra):
    return dict(status=status, pid=pid, **extra)


def pause(player_name=None):
    kwargs = {"player": player_name} if player_name else {}
    return media_tools.MediaControlTool().execute(action="pause", **kwargs)


# ---- ops-8: metadata is read by key --------------------------------------------------------
def test_text_inside_another_field_is_never_taken_for_a_key():
    svc = mps.get_media_service()
    assert svc._parse_metadata(NO_TITLE_FAKE_IN_ALBUM)["title"] == "Real Name"
    assert svc._parse_metadata(NO_TITLE_FAKE_IN_ALBUM)["album"] == "'xesam:title': <'FAKE TITLE'>"
    assert svc._parse_metadata(NO_TITLE_FAKE_IN_COMMENT)["title"] == "Real Name"
    assert svc._parse_metadata(TITLE_HOLDS_FAKE_ALBUM)["album"] == "Unknown"
    assert svc._parse_metadata(ALBUM_BEFORE_REAL_TITLE)["title"] == "Real Title"


def test_metadata_escapes_and_gvariant_forms():
    info = mps.get_media_service()._parse_metadata(ESCAPES)
    assert info["title"] == "Don't say \"hi\""
    assert info["artist"] == "Guns N' Roses, a]>b"
    assert info["album"] == "line1\nline2\tend \x1b \\x"
    assert info["art_url"] == "file:///a/it's.jpg"
    meta = mps.parse_gvariant(ESCAPES)[0]
    assert meta["x:bytes"] == "it's\x01\xe9\n\\"
    assert meta["x:maybe"] is None and meta["x:empty"] == [] and meta["x:dict"] == {}
    assert meta["x:inf"] == float("-inf") and meta["x:byte"] == 3 and meta["x:tuple"] == (1, "a")
    assert meta["x:u64"] == 2 ** 64 - 1
    assert mps.parse_gvariant("(uint32 4242,)") == (4242,)
    assert mps.parse_gvariant("{'k', <5>}") == {"k": 5}


@pytest.mark.parametrize("title", ["''", "'   '"])
def test_an_empty_title_tag_falls_back_to_the_file_name(title):
    raw = "(<{" + URL + ", 'xesam:title': <" + title + ">, 'xesam:artist': <@as []>}>,)"
    info = mps.get_media_service()._parse_metadata(raw)
    assert info["title"] == "Real Name" and info["artist"] == "Unknown"


@pytest.mark.parametrize("raw", [
    "(<{'xesam:title': <'unterminated>}>,)",
    "(<{'xesam:title' <'x'>}>,)",
    "garbage 'xesam:title': <'G'>",
    "(" * 200 + ")" * 200,
    "",
])
def test_unreadable_metadata_gives_defaults_not_a_guess(raw):
    assert mps.get_media_service()._parse_metadata(raw)["title"] == "Unknown"


# ---- ops-9: which player a command lands on ------------------------------------------------
def test_the_vlc_guaardvark_started_comes_first(env):
    ours_running(env)
    bus = use_bus(env, {SPOTIFY: player("Playing"), VLC: player("Paused", USER_VLC_PID),
                        OURS_INSTANCE: player("Playing", OURS_PID)}, order=[VLC, SPOTIFY, OURS_INSTANCE])
    res = pause()
    assert res.success and res.output == f"Paused on vlc.instance{OURS_PID} (the VLC Guaardvark started)"
    assert bus.commands() == [(OURS_INSTANCE, "Pause")]


def test_the_vlc_guaardvark_started_found_under_the_plain_name_by_its_pid(env):
    ours_running(env)
    bus = use_bus(env, {SPOTIFY: player("Playing"), VLC: player("Paused", OURS_PID)})
    assert pause().output == "Paused on vlc (the VLC Guaardvark started)"
    assert bus.commands() == [(VLC, "Pause")]


def test_a_reused_pid_is_not_the_vlc_guaardvark_started(env):
    ours_running(env, start="999")
    bus = use_bus(env, {SPOTIFY: player("Playing"), VLC: player("Paused", OURS_PID)})
    assert pause().output == "Paused on spotify (the only one playing)"
    assert bus.commands() == [(SPOTIFY, "Pause")]


def test_the_vlc_guaardvark_started_not_yet_on_dbus_is_waited_for(env):
    ours_running(env)
    bus = use_bus(env, {SPOTIFY: player("Playing")})
    res = pause()
    assert not res.success and "not on D-Bus yet" in res.error and "spotify" in res.error
    assert bus.commands() == []


@pytest.mark.parametrize("order", [[SPOTIFY, VLC], [VLC, SPOTIFY]])
def test_otherwise_the_only_one_playing_whatever_the_listing_order(env, order):
    bus = use_bus(env, {SPOTIFY: player("Playing"), VLC: player("Paused", USER_VLC_PID)}, order)
    assert pause().output == "Paused on spotify (the only one playing)"
    assert bus.commands() == [(SPOTIFY, "Pause")]


def test_a_lone_player_is_used_even_when_paused(env):
    bus = use_bus(env, {SPOTIFY: player("Paused")})
    assert media_tools.MediaPlayTool().execute().output == "Playing on spotify (the only player open)"
    assert bus.commands() == [(SPOTIFY, "Play")]


@pytest.mark.parametrize("statuses", [("Paused", "Paused"), ("Playing", "Playing")])
def test_no_obvious_player_acts_on_none_and_lists_them(env, statuses):
    bus = use_bus(env, {SPOTIFY: player(statuses[0]), VLC: player(statuses[1], USER_VLC_PID)})
    res = pause()
    assert not res.success and f"spotify ({statuses[0]}), vlc ({statuses[1]})" in res.error
    assert "player=" in res.error
    assert not media_tools.MediaPlayTool().execute().success
    status = media_tools.MediaStatusTool().execute()
    assert status.success and "spotify" in status.output and "vlc" in status.output
    assert bus.commands() == []


def test_no_player_running(env):
    use_bus(env, {})
    assert pause().error == "No media player is running."
    assert media_tools.MediaStatusTool().execute().output == "No media player is running."


def test_status_names_the_player_why_and_the_others(env):
    ours_running(env)
    meta = "(<{'xesam:title': <'Plain Song'>, 'mpris:length': <int64 240600860>}>,)"
    use_bus(env, {SPOTIFY: player("Playing"), OURS_INSTANCE: player("Playing", OURS_PID, meta=meta)})
    lines = media_tools.MediaStatusTool().execute().output.splitlines()
    assert lines[:4] == [f"Player: vlc.instance{OURS_PID}", "Chosen: the VLC Guaardvark started",
                         "Status: Playing", "Title: Plain Song"]
    assert "Length: 4:00" in lines and "Player volume: 50%" in lines and lines[-1] == "Other players: spotify"


@pytest.mark.parametrize("name,expected", [
    ("spotify", SPOTIFY), ("Spotify", SPOTIFY), (SPOTIFY, SPOTIFY), ("vlc", OURS_INSTANCE),
])
def test_named_players(env, name, expected):
    bus = use_bus(env, {SPOTIFY: player("Playing"), OURS_INSTANCE: player("Paused", OURS_PID)})
    res = pause(name)
    assert res.success and "(" not in res.output, res.output
    assert bus.commands() == [(expected, "Pause")]


@pytest.mark.parametrize("name,message", [
    ("nope", "No player named 'nope' is running. Open players: spotify, vlc.instance"),
    ("bad name!", "is not a player name"),
])
def test_unknown_or_malformed_player_names(env, name, message):
    bus = use_bus(env, {SPOTIFY: player("Playing"), OURS_INSTANCE: player("Paused", OURS_PID)})
    res = pause(name)
    assert not res.success and message in res.error
    assert bus.commands() == []


# ---- ops-12: Linux only --------------------------------------------------------------------
@pytest.mark.parametrize("system,shown", [("Darwin", "macOS"), ("Windows", "Windows")])
def test_off_linux_the_tools_are_not_registered_and_refuse(env, monkeypatch, system, shown):
    from backend.tools import tool_registry_init as tri

    monkeypatch.setattr(plat, "os_name", lambda: system)
    registered = []
    monkeypatch.setattr(tri, "register_tool", registered.append)
    monkeypatch.setattr(tri, "_tool_categories", {})
    assert tri.register_media_tools() == [] and registered == []
    for tool, kwargs in [(media_tools.MediaPlayTool, {"query": "jazz"}),
                         (media_tools.MediaControlTool, {"action": "pause"}),
                         (media_tools.MediaVolumeTool, {"level": "50"}),
                         (media_tools.MediaStatusTool, {})]:
        res = tool().execute(**kwargs)
        assert not res.success and "only on Linux" in res.error and f"runs {shown}" in res.error


def test_on_linux_all_four_are_registered(env, monkeypatch):
    from backend.tools import tool_registry_init as tri

    registered = []
    monkeypatch.setattr(tri, "register_tool", registered.append)
    monkeypatch.setattr(tri, "_tool_categories", {})
    assert tri.register_media_tools() == ["media_play", "media_control", "media_volume", "media_status"]
    assert [t.name for t in registered] == ["media_play", "media_control", "media_volume", "media_status"]


def test_missing_gdbus_and_amixer_are_named(env, monkeypatch):
    def missing(cmd, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    monkeypatch.setattr(mps.subprocess, "run", missing)
    assert "gdbus is not installed" in pause().error
    assert "amixer is not installed" in media_tools.MediaVolumeTool().execute().error


# ---- launching and replacing the VLC Guaardvark started -------------------------------------
class Launches:
    def __init__(self, env, delay=0.0):
        self.env, self.delay, self.cmds, self.events = env, delay, [], []
        self.next_pid = 2_000_000_000

    def Popen(self, cmd, **kwargs):
        name = threading.current_thread().name
        self.events.append(("start", name))
        time.sleep(self.delay)
        self.cmds.append(list(cmd))
        self.next_pid += 1
        self.env.procs[self.next_pid] = {"state": "S", "start": str(self.next_pid), "vlc": True}
        self.events.append(("end", name))
        return SimpleNamespace(pid=self.next_pid)


def test_vlc_starts_with_one_instance_off_and_mpris_on(env, monkeypatch):
    launches = Launches(env)
    monkeypatch.setattr(mps.subprocess, "Popen", launches.Popen)
    res = media_tools.MediaPlayTool().execute(directory=str(env.music), shuffle=True)
    assert res.success and "in Guaardvark's VLC (pid 2000000001)" in res.output
    assert launches.cmds[0][1:] == ["--no-one-instance", "--dbus", "--no-metadata-network-access",
                                    "--random", str(env.music.resolve())]
    assert env.pidfile.read_text() == "2000000001 2000000001"


@pytest.mark.parametrize("folder", ["/", "/etc", "/usr/share", "/proc/self"])
def test_system_folders_are_refused(env, folder):
    res = media_tools.MediaPlayTool().execute(directory=folder)
    assert not res.success and "name a folder of music instead" in res.error


def test_our_old_vlc_gets_sigterm_then_sigkill_if_it_stays(env):
    pid = 2_120_000_001
    env.pidfile.parent.mkdir(parents=True)
    env.pidfile.write_text(f"{pid} 500")
    env.procs[pid] = {"state": "S", "start": "500", "vlc": True}
    env.exits_on.add("SIGKILL")
    env.svc._kill_existing_vlc()
    assert env.signals == [(pid, "SIGTERM"), (pid, "SIGKILL")] and not env.pidfile.exists()

    env.signals.clear()
    env.pidfile.write_text(f"{pid} 500")
    env.procs[pid] = {"state": "S", "start": "500", "vlc": True}
    env.exits_on.add("SIGTERM")
    env.svc._kill_existing_vlc()
    assert env.signals == [(pid, "SIGTERM")]


@pytest.mark.parametrize("record,proc", [
    ("2120000001 500", {"state": "S", "start": "501", "vlc": True}),  # PID reused
    ("2120000001 500", {"state": "Z", "start": "500", "vlc": True}),  # already exited
    ("2120000001", {"state": "S", "start": "500", "vlc": True}),      # pid-only record
    ("1 500", None),
    ("0 500", None),
])
def test_nothing_else_is_signalled(env, record, proc):
    env.pidfile.parent.mkdir(parents=True)
    env.pidfile.write_text(record)
    if proc:
        env.procs[2_120_000_001] = proc
    env.svc._kill_existing_vlc()
    assert env.signals == [] and not env.pidfile.exists()


def test_two_plays_at_once_take_turns(env, monkeypatch):
    launches = Launches(env, delay=0.2)
    monkeypatch.setattr(mps.subprocess, "Popen", launches.Popen)
    env.exits_on.add("SIGTERM")
    threads = [threading.Thread(target=env.svc.launch_vlc, kwargs={"directory": str(env.music)}, name=n)
               for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    first, second = launches.events[0][1], launches.events[2][1]
    assert launches.events == [("start", first), ("end", first), ("start", second), ("end", second)]
    assert env.signals == [(2_000_000_001, "SIGTERM")]
    assert env.pidfile.read_text() == "2000000002 2000000002"


def test_pidfile_lives_in_the_cache_folder():
    from backend import config

    assert mps._vlc_pidfile() == mps.Path(config.CACHE_DIR) / "media" / "vlc.pid"


# ---- volume --------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("50", ("50", "")), ("50%", ("50", "")), (" 7.6 ", ("8", "")), ("+10%", ("+10", "")),
    ("- 10 %", ("-10", "")), (80, ("80", "")), (50.0, ("50", "")), ("MUTE", ("mute", "")),
    ("150", ("100", "150% is more than 100, so 100% was used")),
    ("+250%", ("+100", "+250% is more than 100, so +100% was used")),
])
def test_volume_levels_accept_percentages_and_clamp(raw, expected):
    assert mps.normalize_volume_level(raw) == expected


@pytest.mark.parametrize("raw", [-5, True, "abc", "٥٠", "%50"])
def test_volume_levels_refused(raw):
    with pytest.raises(ValueError):
        mps.normalize_volume_level(raw)


def test_volume_tool_says_when_it_clamped(env, monkeypatch):
    calls = []

    def amixer(cmd, **kwargs):
        calls.append(cmd[1:])
        return SimpleNamespace(returncode=0, stdout="Mono: Playback 65536 [100%] [0.00dB] [on]", stderr="")

    monkeypatch.setattr(mps.subprocess, "run", amixer)
    res = media_tools.MediaVolumeTool().execute(level="150")
    assert res.output == "Volume set to 100% (150% is more than 100, so 100% was used)"
    media_tools.MediaVolumeTool().execute(level="+30%")
    assert calls[0] == ["set", "Master", "100%"] and calls[2] == ["set", "Master", "30%+"]
