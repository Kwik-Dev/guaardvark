#!/usr/bin/python3
"""Desktop control for a Wayland session, through the XDG desktop portal.

X11 tools (pyautogui, xdotool) see a black screen on Wayland and reach only
XWayland windows. This bridge holds one RemoteDesktop portal session with the
user's monitors attached as ScreenCast streams, the mechanism GNOME Remote
Desktop and screen sharing use: frames arrive over PipeWire, and pointer and
keyboard events go through the portal. The desktop asks the user once; the
restore token it returns is saved, so later sessions start without asking until
the user revokes it.

It runs under the system Python, which has python3-dbus, python3-gi and the
GStreamer PipeWire plugin; a virtualenv usually cannot see those. It imports
nothing from the backend. backend/services/wayland_desktop.py starts it and
talks to it over a Unix socket, one JSON object per line each way:

    {"op": "status"}
    {"op": "screenshot", "path": "/abs/out.png", "region": [x, y, w, h]}
    {"op": "move", "x": 100, "y": 200}
    {"op": "click", "x": 100, "y": 200, "button": "left", "clicks": 1}
    {"op": "type", "text": "hello", "interval": 0.02}
    {"op": "hotkey", "keys": ["ctrl", "s"]}
    {"op": "scroll", "dy": -3, "dx": 0}
    {"op": "record_start", "path": "/abs/out.mkv", "monitor": 0, "fps": 30}
    {"op": "record_stop"}

A recording takes one monitor (its index in the layout, left to right) or
"all" side by side, cursor included, encoded with x264. A .mkv file survives
an interrupted recording; .mp4 is finalised only by record_stop.

Coordinates are the desktop's logical pixels, monitors laid out as the
compositor reports them, the same space a screenshot is taken in.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time

try:
    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    from PIL import Image
except (ImportError, ValueError) as exc:  # pragma: no cover - environment check
    sys.stderr.write(
        f"wayland bridge: missing system package ({exc}). Install python3-dbus, python3-gi, "
        "gir1.2-gstreamer-1.0, gstreamer1.0-pipewire and python3-pil.\n")
    sys.exit(3)

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
RD = "org.freedesktop.portal.RemoteDesktop"
SC = "org.freedesktop.portal.ScreenCast"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"

DEVICE_KEYBOARD, DEVICE_POINTER = 1, 2
SOURCE_MONITOR = 1
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2

BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}   # evdev BTN_* codes
AXIS_VERTICAL, AXIS_HORIZONTAL = 0, 1

# Named keys as X keysyms; a single character maps through its code point.
KEYSYMS = {
    "enter": 0xff0d, "return": 0xff0d, "tab": 0xff09, "backspace": 0xff08,
    "escape": 0xff1b, "esc": 0xff1b, "delete": 0xffff, "del": 0xffff,
    "insert": 0xff63, "home": 0xff50, "end": 0xff57, "pageup": 0xff55,
    "pagedown": 0xff56, "left": 0xff51, "up": 0xff52, "right": 0xff53, "down": 0xff54,
    "space": 0x20, "ctrl": 0xffe3, "control": 0xffe3, "shift": 0xffe1,
    "alt": 0xffe9, "super": 0xffeb, "win": 0xffeb, "meta": 0xffeb,
    "capslock": 0xffe5, "printscreen": 0xff61, "menu": 0xff67,
    **{f"f{n}": 0xffbe + n - 1 for n in range(1, 13)},
}


def keysym_for_char(ch: str) -> int:
    if ch == "\n":
        return KEYSYMS["return"]
    if ch == "\t":
        return KEYSYMS["tab"]
    cp = ord(ch)
    # Latin-1 keysyms equal their code points; the rest use the Unicode range.
    return cp if (0x20 <= cp <= 0x7e or 0xa0 <= cp <= 0xff) else 0x01000000 + cp


def keysym_for_name(name: str) -> int:
    key = name.strip().lower()
    if key in KEYSYMS:
        return KEYSYMS[key]
    if len(name) == 1:
        return keysym_for_char(name)
    raise ValueError(f"unknown key {name!r}")


class PortalError(RuntimeError):
    pass


class Bridge:
    def __init__(self, token_file: str):
        self.token_file = token_file
        DBusGMainLoop(set_as_default=True)
        Gst.init(None)
        self.bus = dbus.SessionBus()
        portal = self.bus.get_object(PORTAL, PORTAL_PATH)
        self.rd = dbus.Interface(portal, RD)
        self.sc = dbus.Interface(portal, SC)
        self.session = None
        self.streams: list[dict] = []        # node_id, x, y, w, h, frame
        self._frame_lock = threading.Lock()
        self._pipelines = []
        self.closed = False
        self._n = 0
        self.recording = None                # {"pipeline", "path", "started"}

    # --- portal requests ------------------------------------------------

    def _token(self, kind: str) -> str:
        self._n += 1
        return f"guaardvark_{os.getpid()}_{kind}_{self._n}"

    def _request(self, call, *args, options: dict, timeout_s: float) -> dict:
        """Run one portal call and wait for its Request.Response."""
        options = dict(options)
        options["handle_token"] = dbus.String(self._token("req"))
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{options['handle_token']}"
        reply = {}
        loop = GLib.MainLoop()

        def on_response(code, results):
            reply["code"], reply["results"] = int(code), results
            loop.quit()

        # Subscribe before the call so a fast Response is not missed.
        match = self.bus.add_signal_receiver(on_response, signal_name="Response",
                                             dbus_interface=REQUEST, path=path)
        try:
            call(*args, dbus.Dictionary(options, signature="sv"))
            GLib.timeout_add(int(timeout_s * 1000), loop.quit)
            loop.run()
        finally:
            match.remove()
        if "code" not in reply:
            raise PortalError(f"no answer from the desktop within {timeout_s:.0f} s")
        if reply["code"] == 1:
            raise PortalError("the desktop's permission dialog was cancelled")
        if reply["code"] != 0:
            raise PortalError(f"the desktop refused the request (code {reply['code']})")
        return reply["results"]

    def start(self):
        res = self._request(self.rd.CreateSession, options={
            "session_handle_token": dbus.String(self._token("session"))}, timeout_s=15)
        self.session = dbus.ObjectPath(res["session_handle"])
        self.bus.add_signal_receiver(self._on_closed, signal_name="Closed",
                                     dbus_interface=SESSION, path=self.session)

        devices = {"types": dbus.UInt32(DEVICE_KEYBOARD | DEVICE_POINTER),
                   "persist_mode": dbus.UInt32(PERSIST_UNTIL_REVOKED)}
        token = self._read_token()
        if token:
            devices["restore_token"] = dbus.String(token)
        self._request(self.rd.SelectDevices, self.session, options=devices, timeout_s=15)
        self._request(self.sc.SelectSources, self.session, options={
            "types": dbus.UInt32(SOURCE_MONITOR),
            "multiple": dbus.Boolean(True),
            "cursor_mode": dbus.UInt32(CURSOR_EMBEDDED)}, timeout_s=15)
        # Start shows the permission dialog the first time: give the person time.
        res = self._request(self.rd.Start, self.session, "", options={}, timeout_s=180)
        if res.get("restore_token"):
            self._write_token(str(res["restore_token"]))
        if not int(res.get("devices", 0)) & DEVICE_POINTER:
            raise PortalError("pointer control was not granted")
        for node_id, props in res.get("streams", []):
            x, y = (int(v) for v in props.get("position", (0, 0)))
            w, h = (int(v) for v in props.get("size", (0, 0)))
            self.streams.append({"node_id": int(node_id), "x": x, "y": y, "w": w, "h": h,
                                 "frame": None})
        if not self.streams:
            raise PortalError("no monitor was shared")
        fd = self.sc.OpenPipeWireRemote(self.session, dbus.Dictionary({}, signature="sv")).take()
        for stream in self.streams:
            self._open_stream(fd, stream)

    def _open_stream(self, fd: int, stream: dict):
        pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={stream['node_id']} always-copy=true keepalive-time=1000 "
            "! videoconvert ! video/x-raw,format=RGB "
            "! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false")
        sink = pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample, stream)
        pipeline.set_state(Gst.State.PLAYING)
        self._pipelines.append(pipeline)

    def _on_sample(self, sink, stream):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                frame = Image.frombytes("RGB", (w, h), bytes(info.data))
            finally:
                buf.unmap(info)
            with self._frame_lock:
                stream["frame"] = frame
        return Gst.FlowReturn.OK

    def record_start(self, path: str, monitor="all", fps: int = 30) -> dict:
        """Record from the frames this bridge already receives, pushed at a steady
        rate. The desktop streams send a frame only when the screen changes, so a
        second consumer joining a still monitor would record nothing; re-sending the
        latest frame records a still screen as a still picture."""
        if self.recording:
            raise RuntimeError(f"already recording to {self.recording['path']}")
        fps = max(1, min(int(fps), 60))
        ordered = sorted(self.streams, key=lambda s: (s["x"], s["y"]))
        picked = ordered if monitor in (None, "all") else [ordered[int(monitor)]]
        bx, by = min(s["x"] for s in picked), min(s["y"] for s in picked)
        w = max(s["x"] + s["w"] for s in picked) - bx
        h = max(s["y"] + s["h"] for s in picked) - by
        mux = "mp4mux" if path.lower().endswith(".mp4") else "matroskamux"
        pipeline = Gst.parse_launch(
            f"appsrc name=src is-live=true format=time "
            f"caps=video/x-raw,format=RGB,width={w},height={h},framerate={fps}/1 "
            f"! videoconvert ! video/x-raw,format=I420 "
            f"! x264enc pass=qual quantizer=20 speed-preset=veryfast key-int-max={fps * 2} "
            f"! {mux} ! filesink location={path}")
        src = pipeline.get_by_name("src")
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("the recording pipeline did not start")
        rec = {"pipeline": pipeline, "path": path, "started": time.monotonic(), "stop": threading.Event()}

        def compose():
            canvas = Image.new("RGB", (w, h))
            with self._frame_lock:
                frames = [(s, s["frame"]) for s in picked]
            for s, frame in frames:
                if frame is None:
                    continue
                if frame.size != (s["w"], s["h"]):
                    frame = frame.resize((s["w"], s["h"]))
                canvas.paste(frame, (s["x"] - bx, s["y"] - by))
            return canvas.tobytes()

        def pusher():
            step = Gst.SECOND // fps
            n = 0
            t0 = time.monotonic()
            while not rec["stop"].is_set():
                buf = Gst.Buffer.new_wrapped(compose())
                buf.pts, buf.duration = n * step, step
                if src.emit("push-buffer", buf) != Gst.FlowReturn.OK:
                    break
                n += 1
                # Hold the frame rate against the wall clock, dropping no frame numbers.
                delay = t0 + n / fps - time.monotonic()
                if delay > 0:
                    rec["stop"].wait(delay)
            src.emit("end-of-stream")

        rec["thread"] = threading.Thread(target=pusher, daemon=True)
        rec["thread"].start()
        self.recording = rec
        return {"path": path, "monitors": [{k: s[k] for k in ("x", "y", "w", "h")} for s in picked],
                "size": [w, h], "fps": fps}

    def record_stop(self) -> dict:
        rec = self.recording
        if not rec:
            raise RuntimeError("not recording")
        self.recording = None
        rec["stop"].set()
        rec["thread"].join(timeout=10)
        pipeline = rec["pipeline"]
        # The pusher ends the stream; the muxer then writes its index. Without it an
        # .mp4 is unreadable.
        msg = pipeline.get_bus().timed_pop_filtered(
            30 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        pipeline.set_state(Gst.State.NULL)
        if msg is not None and msg.type == Gst.MessageType.ERROR:
            err, _ = msg.parse_error()
            raise RuntimeError(f"the recording failed: {err.message}")
        return {"path": rec["path"], "seconds": round(time.monotonic() - rec["started"], 1),
                "bytes": os.path.getsize(rec["path"]) if os.path.exists(rec["path"]) else 0,
                "finalised": msg is not None}

    def _on_closed(self, *_):
        # The person stopped sharing from the top bar, or the portal went away.
        self.closed = True

    def _read_token(self) -> str | None:
        try:
            with open(self.token_file) as fh:
                return fh.read().strip() or None
        except OSError:
            return None

    def _write_token(self, token: str):
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token)

    def stop(self):
        if self.recording:
            try:
                self.record_stop()
            except Exception:  # noqa: BLE001 - closing anyway
                pass
        for p in self._pipelines:
            p.set_state(Gst.State.NULL)
        if self.session is not None and not self.closed:
            try:
                dbus.Interface(self.bus.get_object(PORTAL, self.session), SESSION).Close()
            except dbus.DBusException:
                pass

    # --- geometry -------------------------------------------------------

    def bounds(self) -> tuple[int, int, int, int]:
        x0 = min(s["x"] for s in self.streams)
        y0 = min(s["y"] for s in self.streams)
        x1 = max(s["x"] + s["w"] for s in self.streams)
        y1 = max(s["y"] + s["h"] for s in self.streams)
        return x0, y0, x1 - x0, y1 - y0

    def stream_at(self, x: float, y: float) -> dict:
        for s in self.streams:
            if s["x"] <= x < s["x"] + s["w"] and s["y"] <= y < s["y"] + s["h"]:
                return s
        raise ValueError(f"({x:g}, {y:g}) is outside every shared monitor {self.layout()}")

    def layout(self) -> list[dict]:
        return [{k: s[k] for k in ("x", "y", "w", "h")} for s in self.streams]

    # --- operations (run on the GLib thread) ----------------------------

    def screenshot(self, path: str, region=None) -> dict:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self._frame_lock:
                if all(s["frame"] is not None for s in self.streams):
                    frames = [(s, s["frame"].copy()) for s in self.streams]
                    break
            time.sleep(0.05)
        else:
            raise RuntimeError("no frame from the screen yet")
        bx, by, bw, bh = self.bounds()
        canvas = Image.new("RGB", (bw, bh))
        for s, frame in frames:
            if frame.size != (s["w"], s["h"]):     # scaled monitor: frames are device pixels
                frame = frame.resize((s["w"], s["h"]))
            canvas.paste(frame, (s["x"] - bx, s["y"] - by))
        if region:
            rx, ry, rw, rh = (int(v) for v in region)
            canvas = canvas.crop((rx - bx, ry - by, rx - bx + rw, ry - by + rh))
        canvas.save(path)
        return {"path": path, "width": canvas.width, "height": canvas.height}

    def move(self, x: float, y: float):
        s = self.stream_at(x, y)
        self.rd.NotifyPointerMotionAbsolute(self.session, dbus.Dictionary({}, signature="sv"),
                                            dbus.UInt32(s["node_id"]),
                                            dbus.Double(x - s["x"]), dbus.Double(y - s["y"]))

    def button(self, name: str, pressed: bool):
        code = BUTTONS.get(name)
        if code is None:
            raise ValueError(f"unknown button {name!r}")
        self.rd.NotifyPointerButton(self.session, dbus.Dictionary({}, signature="sv"),
                                    dbus.Int32(code), dbus.UInt32(1 if pressed else 0))

    def key(self, keysym: int, pressed: bool):
        self.rd.NotifyKeyboardKeysym(self.session, dbus.Dictionary({}, signature="sv"),
                                     dbus.Int32(keysym), dbus.UInt32(1 if pressed else 0))

    def scroll(self, axis: int, steps: int):
        self.rd.NotifyPointerAxisDiscrete(self.session, dbus.Dictionary({}, signature="sv"),
                                          dbus.UInt32(axis), dbus.Int32(steps))


def handle(bridge: Bridge, req: dict) -> dict:
    op = req.get("op")
    if op == "status":
        return {"layout": bridge.layout(), "bounds": list(bridge.bounds())}
    if op == "screenshot":
        return bridge.screenshot(req["path"], req.get("region"))
    if op == "move":
        bridge.move(float(req["x"]), float(req["y"]))
        return {}
    if op == "click":
        bridge.move(float(req["x"]), float(req["y"]))
        time.sleep(0.03)
        for _ in range(max(1, int(req.get("clicks", 1)))):
            bridge.button(req.get("button", "left"), True)
            time.sleep(0.03)
            bridge.button(req.get("button", "left"), False)
            time.sleep(0.06)
        return {}
    if op == "type":
        interval = max(0.0, float(req.get("interval", 0.02)))
        for ch in str(req["text"]):
            sym = keysym_for_char(ch)
            bridge.key(sym, True)
            bridge.key(sym, False)
            time.sleep(interval)
        return {"typed": len(str(req["text"]))}
    if op == "hotkey":
        syms = [keysym_for_name(k) for k in req["keys"]]
        for sym in syms:
            bridge.key(sym, True)
            time.sleep(0.02)
        for sym in reversed(syms):
            bridge.key(sym, False)
            time.sleep(0.02)
        return {}
    if op == "scroll":
        if int(req.get("dy", 0)):
            bridge.scroll(AXIS_VERTICAL, int(req["dy"]))
        if int(req.get("dx", 0)):
            bridge.scroll(AXIS_HORIZONTAL, int(req["dx"]))
        return {}
    if op == "record_start":
        return bridge.record_start(req["path"], req.get("monitor", "all"), req.get("fps", 30))
    if op == "record_stop":
        return bridge.record_stop()
    raise ValueError(f"unknown op {op!r}")


def serve(bridge: Bridge, sock_path: str, idle_s: float):
    loop = GLib.MainLoop()
    last_use = [time.monotonic()]

    def run_on_loop(fn):
        """Portal calls belong to the GLib thread; wait for the answer."""
        box, done = {}, threading.Event()

        def call():
            try:
                box["value"] = fn()
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                box["error"] = exc
            done.set()
            return False

        GLib.idle_add(call)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["value"]

    def client(conn):
        with conn, conn.makefile("rwb") as fh:
            for line in fh:
                last_use[0] = time.monotonic()
                try:
                    req = json.loads(line)
                    out = {"ok": True, **run_on_loop(lambda r=req: handle(bridge, r))}
                except Exception as exc:  # noqa: BLE001
                    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                fh.write((json.dumps(out) + "\n").encode())
                fh.flush()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv.bind(sock_path)
    os.chmod(sock_path, 0o600)
    srv.listen(8)

    def acceptor():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=client, args=(conn,), daemon=True).start()

    threading.Thread(target=acceptor, daemon=True).start()

    def watchdog():
        idle = idle_s and not bridge.recording and time.monotonic() - last_use[0] > idle_s
        if bridge.closed or idle:
            loop.quit()
            return False
        return True

    GLib.timeout_add_seconds(2, watchdog)
    try:
        loop.run()
    finally:
        srv.close()
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--socket", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--idle-seconds", type=float, default=600.0,
                    help="close the session after this long without a request (0: never)")
    args = ap.parse_args()
    bridge = Bridge(args.token_file)
    try:
        bridge.start()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"wayland bridge: {exc}\n")
        bridge.stop()
        sys.exit(2)
    sys.stdout.write(json.dumps({"ready": True, "layout": bridge.layout()}) + "\n")
    sys.stdout.flush()
    try:
        serve(bridge, args.socket, args.idle_seconds)
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
