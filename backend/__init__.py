# Enable backend as a package for tests and expose Socket.IO instance
from .no_netrc import install as _install_no_netrc

# Before anything opens a connection: no request this process makes sends a
# ~/.netrc login (see backend/no_netrc.py).
_install_no_netrc()

from .socketio_events import socketio  # noqa: E402
