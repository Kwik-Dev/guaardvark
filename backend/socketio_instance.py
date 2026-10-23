import logging

from flask_socketio import SocketIO

from backend.utils.cors_policy import socketio_origin_allowed

# Largest decoded chat attachment the server accepts: the base64 ``image`` (or
# the ``images`` list, counted together) on a ``chat:send`` packet or a
# ``POST /api/chat/unified`` body. 16 MB fits a phone photo (a 48 MP HEIC/JPEG
# straight off a current phone is 4-12 MB). Anything larger is answered with
# ``chat:error`` / HTTP 413 instead of vanishing. The chat UI reads this value
# from ``GET /api/chat/config`` before it sends.
CHAT_ATTACHMENT_MAX_BYTES = 16 * 1024 * 1024

# Socket.IO packet ceiling. python-engineio closes the connection on any packet
# over this size before a handler runs and offers no hook to answer the client,
# so the ceiling must carry the largest allowed attachment as base64 (4/3 of
# the bytes) plus the message text and JSON envelope. At the previous 1 MB a
# 1.3 MB photo never reached ``handle_chat_send`` and the client heard nothing.
SOCKET_MAX_HTTP_BUFFER_SIZE = CHAT_ATTACHMENT_MAX_BYTES * 4 // 3 + 1024 * 1024


def chat_attachments(payload) -> list:
    """The pictures attached to one chat turn, in the order the user attached them.

    A ``chat:send`` packet or a ``POST /api/chat/unified`` body carries them as
    ``images`` (a list of base64 strings) and, for older clients, ``image`` (one
    string). ``images`` wins when both are present; the first entry is the
    turn's primary picture (the one image tools edit).
    """
    if not isinstance(payload, dict):
        return []
    many = payload.get("images")
    if isinstance(many, (list, tuple)):
        found = [img for img in many if isinstance(img, (str, bytes)) and img]
        if found:
            return found
    one = payload.get("image")
    return [one] if isinstance(one, (str, bytes)) and one else []


def chat_attachment_size_bytes(image_data) -> int:
    """Decoded size of a base64 chat attachment, computed without decoding it.

    Accepts the bare base64 string the chat UI sends or a ``data:`` URL; a
    list of them counts as their total; any other value counts as no
    attachment. Base64 carries 3 bytes in 4 characters, less one byte per
    ``=`` of padding.
    """
    if isinstance(image_data, (list, tuple)):
        return sum(chat_attachment_size_bytes(item) for item in image_data)
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
    """Plain-language rejection for an attachment over the limit, else ``None``.

    Several pictures on one turn share the limit, so the packet that carries
    them still fits the Socket.IO buffer sized for one.
    """
    size = chat_attachment_size_bytes(image_data)
    if size <= CHAT_ATTACHMENT_MAX_BYTES:
        return None
    limit_mb = CHAT_ATTACHMENT_MAX_BYTES // (1024 * 1024)
    if isinstance(image_data, (list, tuple)) and len(image_data) > 1:
        return (
            f"The {len(image_data)} attached images total {size / (1024 * 1024):.1f} MB; "
            f"the limit is {limit_mb} MB. Remove one or resize them and try again."
        )
    return (
        f"Attachment is {size / (1024 * 1024):.1f} MB; the limit is "
        f"{limit_mb} MB. Resize the image and try again."
    )


# Configure SocketIO with memory leak prevention
socketio = SocketIO(
    # The origins Flask's CORS allows (backend/utils/cors_policy.py). Engine.IO
    # refuses a request whose Origin is not one of them; the CLI's polling
    # requests send no Origin and pass.
    cors_allowed_origins=socketio_origin_allowed,
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
