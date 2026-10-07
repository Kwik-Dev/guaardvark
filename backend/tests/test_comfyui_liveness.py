"""A ComfyUI that stops answering HTTP while its port still accepts connections
is busy (loading a model, sampling), not down; only a refused connection is down.
The stale-job reaper cancels a render's batch on "down", so this line matters."""

import http.server
import os
import socket  # test fixtures only: a listener that never answers
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.utils.comfyui_liveness import BUSY, DOWN, UP, probe_comfyui


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_answering_http_is_up():
    class Ok(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert probe_comfyui(f"http://127.0.0.1:{server.server_port}", http_timeout=2) == UP
    finally:
        server.shutdown()


def test_accepting_but_silent_is_busy():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)  # accepts at the kernel level, never answers
    try:
        port = listener.getsockname()[1]
        assert probe_comfyui(f"http://127.0.0.1:{port}", http_timeout=0.3, connect_timeout=0.5) == BUSY
    finally:
        listener.close()


def test_refused_connection_is_down():
    port = _free_port()
    assert probe_comfyui(f"http://127.0.0.1:{port}", http_timeout=0.3, connect_timeout=0.5) == DOWN
