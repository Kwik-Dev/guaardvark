"""Path containment helpers for tools that touch the filesystem.

``startswith`` checks on ``abspath`` were used before and are wrong in two
ways: "/tmpX" passes for "/tmp", and symlinks inside an allowed root can
point anywhere. These helpers resolve symlinks and compare path components.
"""

import fnmatch
import os
from typing import Iterable

# Files an agent should never read or list the contents of, even inside an
# allowed directory (they hold credentials for the whole installation).
SENSITIVE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    ".netrc", ".pgpass", "credentials", "credentials.*", "mcp_servers.json", ".git-credentials",
)


def resolve(path: str, base: str = None) -> str:
    """Absolute, symlink-resolved path (relative paths resolve against ``base``)."""
    path = os.path.expanduser(str(path))
    if base and not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.realpath(path)


def is_within(path: str, roots: Iterable[str], base: str = None) -> bool:
    """True if ``path`` (after resolving symlinks) is inside one of ``roots``."""
    target = resolve(path, base)
    for root in roots:
        if not root:
            continue
        root_real = resolve(root)
        try:
            if os.path.commonpath([target, root_real]) == root_real:
                return True
        except ValueError:  # different drives on Windows
            continue
    return False


def is_sensitive(path: str) -> bool:
    name = os.path.basename(str(path).rstrip("/"))
    return any(fnmatch.fnmatch(name, p) for p in SENSITIVE_PATTERNS)


def safe_join(base: str, *parts: str) -> str:
    """Join ``parts`` onto ``base`` and refuse results that escape ``base``."""
    candidate = os.path.join(base, *[str(p) for p in parts])
    if not is_within(candidate, [base]):
        raise ValueError(f"Path escapes {base}: {os.path.join(*[str(p) for p in parts])}")
    return candidate
