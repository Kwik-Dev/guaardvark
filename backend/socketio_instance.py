import logging
import os

from flask_socketio import SocketIO

# Configure SocketIO with secure CORS settings
FRONTEND_URL = os.getenv("VITE_FRONTEND_URL", "http://localhost:5173")
_FLASK_PORT = os.getenv("FLASK_PORT", os.getenv("PORT", "5000"))
_BACKEND_ORIGINS = [
    f"http://localhost:{_FLASK_PORT}",
    f"http://127.0.0.1:{_FLASK_PORT}",
]

# Environment-specific CORS configuration
if os.getenv("FLASK_ENV") == "production":
    allowed_origins = [FRONTEND_URL] + _BACKEND_ORIGINS
else:
    allowed_origins = [
        FRONTEND_URL,
        "http://localhost:5173",  # Vite default
        "http://localhost:5175",  # Vite alternate
        "http://localhost:3000",  # React default
        "http://127.0.0.1:5173",  # Alternative localhost
        "http://127.0.0.1:5175",  # Alternative localhost
        "http://127.0.0.1:3000",  # Alternative localhost
    ] + _BACKEND_ORIGINS

# Always allow LAN private-IP origins for local workstation use (phone/tablet/browser
# on the same network accessing the printed LAN IP + VITE_PORT). This enables
# SocketIO (real-time chat, progress, voice streaming) when the client Origin is
# http://192.168.x.x:port etc. Patterns are the same set used (under interconnector
# master) for Flask CORS. Ungated here because this is a personal offline machine.
lan_patterns = [
    r"http://192\.168\.\d+\.\d+:\d+",
    r"http://10\.\d+\.\d+\.\d+:\d+",
    r"http://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+:\d+",
    r"https://192\.168\.\d+\.\d+:\d+",
    r"https://10\.\d+\.\d+\.\d+:\d+",
    r"https://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+:\d+",
]
allowed_origins = lan_patterns + allowed_origins

# Largest decoded chat attachment the server accepts: the base64 ``image`` on a
# ``chat:send`` packet or a ``POST /api/chat/unified`` body. 16 MB fits a phone
# photo (a 48 MP HEIC/JPEG straight off a current phone is 4-12 MB). Anything
# larger is answered with ``chat:error`` / HTTP 413 instead of vanishing. The
# chat UI reads this value from ``GET /api/chat/config`` before it sends.
CHAT_ATTACHMENT_MAX_BYTES = 16 * 1024 * 1024

# Socket.IO packet ceiling. python-engineio closes the connection on any packet
# over this size before a handler runs and offers no hook to answer the client,
# so the ceiling must carry the largest allowed attachment as base64 (4/3 of
# the bytes) plus the message text and JSON envelope. At the previous 1 MB a
# 1.3 MB photo never reached ``handle_chat_send`` and the client heard nothing.
SOCKET_MAX_HTTP_BUFFER_SIZE = CHAT_ATTACHMENT_MAX_BYTES * 4 // 3 + 1024 * 1024


def chat_attachment_size_bytes(image_data) -> int:
    """Decoded size of a base64 chat attachment, computed without decoding it.

    Accepts the bare base64 string the chat UI sends or a ``data:`` URL; any
    other value counts as no attachment. Base64 carries 3 bytes in 4
    characters, less one byte per ``=`` of padding.
    """
    if isinstance(image_data, bytes):
        image_data = image_data.decode("ascii", "ignore")
    if not isinstance(image_data, str):
        return 0
    if image_data.startswith("data:"):
        image_data = image_data.split(",", 1)[-1]
    encoded = "".join(image_data.split())
    padding = len(encoded) - len(encoded.rstrip("="))
    return max(0, len(encoded) * 3 // 4 - padding)


def chat_attachment_too_large(image_data) -> str | None:
    """Plain-language rejection for an attachment over the limit, else ``None``."""
    size = chat_attachment_size_bytes(image_data)
    if size <= CHAT_ATTACHMENT_MAX_BYTES:
        return None
    return (
        f"Attachment is {size / (1024 * 1024):.1f} MB; the limit is "
        f"{CHAT_ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB. Resize the image and try again."
    )


# Configure SocketIO with memory leak prevention
socketio = SocketIO(
    cors_allowed_origins=allowed_origins,
    ping_timeout=60,  # 60 second ping timeout
    ping_interval=25,  # 25 second ping interval
    max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_SIZE,
    async_mode='threading',  # Use threading for better memory management
    # manage_session=False is REQUIRED with Werkzeug >= 3.1: Flask-SocketIO 5.3.6's
    # managed-session path does `ctx.session = session_obj`, but Werkzeug 3.1 made
    # RequestContext.session a read-only property → every Socket.IO event (incl.
    # `connect`) raised AttributeError("property 'session' ... has no setter"), so NO
    # client could connect (chat:thinking/chat:complete never delivered → thinking
    # trail only appeared after a refresh). With manage_session=False, Flask-SocketIO
    # lets Flask own the session (this app is sessionless anyway), skipping the broken
    # assignment. No dependency change required.
    manage_session=False,
    logger=False,  # Disabled to prevent log flooding
    engineio_logger=False  # Disabled to prevent log flooding
)
logger = logging.getLogger(__name__)
