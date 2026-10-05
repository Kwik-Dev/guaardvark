"""Guard and audit for `guaardvark api`, the generic REST escape hatch (CLI_PLAN D5).

Not a command module: it has no `COMMAND_NAME`, `registry._candidate_modules` skips a
leading underscore, and the fork contract tests skip it the same way (see
`test_fork_readonly_contract._fork_modules`). Both facts are load-bearing. This is the
one file allowed to *name* the decision-class routes that every other fork command is
forbidden to call, and keeping the list here means a contributor editing `api.py`
cannot quietly widen the guard without coming here to do it.

Tier B, decided 2026-10-05:

  * read methods are free;
  * every write method needs `--yes`;
  * a decision-class route needs `--yes` even when it is a read, so a future
    GET-shaped approval cannot slip through on the read path;
  * every attempt -- allowed, refused, failed or dry-run -- is appended to a local
    JSONL audit log, because the point of an escape hatch is that you can see what
    came out of it.

The audit log is client-side on purpose: the CLI records what *it* did, that works when
`--server` points at another machine, and it needs no backend change. It stores the
body's byte size rather than its content, since a body can carry a prompt, a caption or
a credential.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llx.client import LlxError

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALL_METHODS = READ_METHODS | WRITE_METHODS

# Route segments that are a person's decision rather than a data operation. Matched
# *segment-exactly*, not as substrings: `/api/connections/publishes` is a read and must
# not be dragged through the gate because it happens to contain "publish", while
# `/api/production/3/storyboard/approve` and `/api/wordpress/process/queue/execute` are
# exactly what this is for.
#
# Kept in step with `test_fork_readonly_contract._DECISION_MARKERS`: that list stops the
# *named* commands from acquiring these routes silently, and this one makes the generic
# command say so out loud and demand `--yes`.
#
# A missed class costs nothing (every write needs `--yes` regardless); a false positive
# would put `--yes` in front of a read, so the list stays deliberate rather than generous.
DECISION_SEGMENTS = frozenset(
    {
        "approve",
        "reject",
        "decide",
        "apply",
        "release-held",
        "dispatch",
        "confirm",
        "publish",
        "trigger",
        "execute",
    }
)

REQUIRED_PREFIX = "/api/"
AUDIT_FILENAME = "api-audit.jsonl"


@dataclass(frozen=True)
class Verdict:
    """What the guard decided about one `(method, path)` pair."""

    method: str
    path: str
    is_write: bool
    is_decision: bool

    @property
    def needs_yes(self) -> bool:
        return self.is_write or self.is_decision

    @property
    def kind(self) -> str:
        if self.is_decision:
            return "decision"
        return "write" if self.is_write else "read"

    @property
    def why(self) -> str:
        if self.is_decision:
            return "a decision a person normally makes in the Studio"
        if self.is_write:
            return "a write"
        return "a read"


def decision_segments_in(path: str) -> list[str]:
    """The decision-class segments present in a path, if any."""
    bare = path.split("?", 1)[0].split("#", 1)[0]
    return [
        part
        for part in bare.strip("/").split("/")
        if part and part.lower() in DECISION_SEGMENTS
    ]


def is_decision_path(path: str) -> bool:
    """True when any path segment is a decision class."""
    return bool(decision_segments_in(path))


def classify(method: str, path: str) -> Verdict:
    """Validate the request shape and decide whether it needs `--yes`.

    Raises `LlxError` for anything this command must not send, so the caller reports it
    exactly the way it reports a backend 4xx -- one error path, not two.
    """
    verb = (method or "").strip().upper()
    if verb not in ALL_METHODS:
        raise LlxError(
            f"Unsupported method {method!r}. Use one of: " + ", ".join(sorted(ALL_METHODS))
        )

    target = (path or "").strip()
    if not target:
        raise LlxError("A request path is required, for example /api/routes")
    if "://" in target:
        raise LlxError(
            f"Give the backend path, not a URL (got {target!r}). The client already knows "
            "the server and `--server` changes it; an absolute URL would bypass the "
            "backend's own gate and audit path."
        )
    if not target.startswith("/"):
        target = "/" + target

    # A query string is allowed on the path; the prefix check reads the path alone.
    bare = target.split("?", 1)[0].split("#", 1)[0]
    if not bare.startswith(REQUIRED_PREFIX):
        raise LlxError(
            f"{bare!r} is outside the backend API. Paths must start with "
            f"{REQUIRED_PREFIX!r} -- that is what keeps this command away from plugin "
            "ports and the non-API surface. Run `guaardvark api routes` for what exists."
        )

    lowered = bare.lower()
    return Verdict(
        method=verb,
        path=target,
        is_write=verb in WRITE_METHODS,
        is_decision=is_decision_path(lowered),
    )


def with_query(path: str, pairs: list[str] | None) -> str:
    """Append repeatable `--query key=value` pairs to a path that may already have one."""
    if not pairs:
        return path
    from urllib.parse import urlencode

    extra: list[tuple[str, str]] = []
    for pair in pairs:
        key, sep, value = (pair or "").partition("=")
        if not sep or not key.strip():
            raise LlxError(f"--query expects key=value, got {pair!r}")
        extra.append((key.strip(), value))
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{urlencode(extra)}"


def audit_path() -> Path:
    """`<GUAARDVARK_DIR>/api-audit.jsonl`, beside the CLI's own config."""
    from llx.config import CONFIG_DIR

    return Path(CONFIG_DIR) / AUDIT_FILENAME


def body_size(body: Any) -> int | None:
    """Byte length of a JSON-serialisable body, or None. Never the content itself."""
    if body is None:
        return None
    try:
        return len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        return None


def record(
    verdict: Verdict,
    *,
    server: str,
    outcome: str,
    status: int | None = None,
    error: str | None = None,
    body: Any = None,
    duration_ms: int | None = None,
    approved: bool = False,
) -> Path | None:
    """Append one JSONL line; return the file written, or None if it could not be.

    Never raises. An unwritable audit log must not eat a request that was already sent
    -- the caller reports the miss as a warning instead.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server": server,
        "method": verdict.method,
        "path": verdict.path,
        "kind": verdict.kind,
        "needs_yes": verdict.needs_yes,
        "approved": approved,
        "outcome": outcome,
        "status": status,
        "duration_ms": duration_ms,
        "body_bytes": body_size(body),
        "error": error,
    }
    path = audit_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return path


def read_audit(limit: int = 20, *, decisions_only: bool = False) -> list[dict]:
    """The most recent audit entries, oldest first. A missing log is an empty list."""
    path = audit_path()
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return []
    if decisions_only:
        entries = [e for e in entries if e.get("kind") == "decision"]
    return entries[-max(1, limit):]
