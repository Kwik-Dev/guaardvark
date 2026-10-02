"""The reboot log server (backend/utils/reboot_log_server.py) that the reboot
page reads while the backend restarts.

It listens on loopback only, only this install's frontend may read the log,
and only POST /shutdown stops it (a GET, or a POST from another page's
origin, does not). Runs the server's main() in a thread on a free loopback
port with a stand-in log file; nothing leaves the machine.
"""

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from backend.utils import reboot_log_server

OWN = "http://localhost:5173"
OTHER = "https://evil.example"


def _call(port, method, path, origin=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=b"" if method == "POST" else None)
    if origin:
        req.add_header("Origin", origin)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read().decode()


@pytest.fixture
def server(tmp_path, monkeypatch):
    log = tmp_path / "reboot.log"
    log.write_text("line one\nline two\n")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    bound = []

    class RecordingServer(reboot_log_server.HTTPServer):
        def __init__(self, address, handler):
            bound.append(address[0])
            super().__init__(address, handler)

    monkeypatch.setattr(reboot_log_server, "HTTPServer", RecordingServer)
    monkeypatch.setattr(sys, "argv", [
        "reboot_log_server.py", "--port", str(port), "--log-file", str(log),
        "--timeout", "30", "--allow-origin", OWN,
    ])
    thread = threading.Thread(target=reboot_log_server.main, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port, thread, bound
    if thread.is_alive():
        _call(port, "POST", "/shutdown")
        thread.join(5)


def test_it_listens_on_loopback_only(server):
    _, _, bound = server
    assert bound == ["127.0.0.1"]


def test_this_installs_frontend_reads_the_log(server):
    port, _, _ = server
    status, headers, body = _call(port, "GET", "/log?offset=0", OWN)
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == OWN
    assert json.loads(body)["content_lines"] == ["line one", "line two"]


def test_another_page_cannot_read_it(server):
    port, _, _ = server
    _, headers, _ = _call(port, "GET", "/log", OTHER)
    assert headers.get("Access-Control-Allow-Origin") is None


def test_get_shutdown_stops_nothing(server):
    port, thread, _ = server
    assert _call(port, "GET", "/shutdown")[0] == 405
    assert _call(port, "GET", "/log")[0] == 200
    assert thread.is_alive()


def test_another_pages_shutdown_is_refused(server):
    port, thread, _ = server
    assert _call(port, "POST", "/shutdown", OTHER)[0] == 403
    assert _call(port, "GET", "/log")[0] == 200
    assert thread.is_alive()


def test_a_page_rebound_to_this_machine_can_neither_read_nor_stop_it(server, monkeypatch):
    # Same-origin with itself, such a page needs no CORS; its Host gives it away.
    monkeypatch.delenv("VITE_ALLOWED_HOSTS", raising=False)
    port, thread, _ = server
    rebound = f"evil.example:{port}"
    status, _, body = _call(port, "GET", "/log?offset=0", f"http://{rebound}", host=rebound)
    assert status == 421 and "line one" not in body
    assert json.loads(body)["code"] == "host_not_allowed"
    assert _call(port, "POST", "/shutdown", f"http://{rebound}", host=rebound)[0] == 421
    assert _call(port, "GET", "/log", host=f"localhost:{port}")[0] == 200
    assert thread.is_alive()


def test_it_runs_as_a_script_without_the_backend_package(tmp_path):
    # reboot_api.py starts it as a script while the backend stops; the Host
    # rule must load without importing the backend package.
    import subprocess
    from pathlib import Path

    script = Path(reboot_log_server.__file__)
    code = (
        "import sys, runpy; sys.modules['backend'] = None; "
        f"sys.path.insert(0, {str(script.parent)!r}); sys.argv = ['reboot_log_server.py', '--help']; "
        f"runpy.run_path({str(script)!r}, run_name='__main__')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--allow-origin" in result.stdout


@pytest.mark.parametrize("origin", [None, OWN])
def test_post_shutdown_from_the_backend_or_the_reboot_page_stops_it(server, origin):
    port, thread, _ = server
    status, headers, body = _call(port, "POST", "/shutdown", origin)
    assert status == 200 and json.loads(body) == {"ok": True}
    if origin:
        assert headers.get("Access-Control-Allow-Origin") == OWN
    thread.join(5)
    assert not thread.is_alive()
