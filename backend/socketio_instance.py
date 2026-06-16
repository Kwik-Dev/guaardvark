import logging
import os

from flask_socketio import SocketIO

# Configure SocketIO with secure CORS settings
FRONTEND_URL = os.getenv("VITE_FRONTEND_URL", "http://localhost:5173")

# Environment-specific CORS configuration
if os.getenv("FLASK_ENV") == "production":
    allowed_origins = [FRONTEND_URL]
else:
    allowed_origins = [
        FRONTEND_URL,
        "http://localhost:5173",  # Vite default
        "http://localhost:5175",  # Vite alternate
        "http://localhost:3000",  # React default
        "http://127.0.0.1:5173",  # Alternative localhost
        "http://127.0.0.1:5175",  # Alternative localhost
        "http://127.0.0.1:3000",  # Alternative localhost
    ]

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

# Configure SocketIO with memory leak prevention
socketio = SocketIO(
    cors_allowed_origins=allowed_origins,
    ping_timeout=60,  # 60 second ping timeout
    ping_interval=25,  # 25 second ping interval
    max_http_buffer_size=1024 * 1024,  # 1MB max buffer size
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
