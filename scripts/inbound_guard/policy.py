"""Mode resolution and the severity-to-verdict policy."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional

from .model import MODES, SEVERITY_RANK, Finding
from .sources import git

ENV_MODE = "GUAARDVARK_INBOUND_GUARD"


def normalize_mode(value: Optional[str], *, origin: str = "") -> str:
    """Map a configured value to a mode.

    An unrecognised value becomes observe, not off and not enforce: a typo must
    neither silently disable the guard nor start refusing work nobody asked it to.
    """
    if value is None or not value.strip():
        return "off"
    mode = value.strip().lower()
    if mode in MODES:
        return mode
    print(f"inbound guard: unknown mode {value!r}{' from ' + origin if origin else ''}; using observe",
          file=sys.stderr)
    return "observe"


def git_mode(repo: Path) -> str:
    """The mode git hooks and the CLI use: environment, then git config, then off."""
    if os.environ.get(ENV_MODE):
        return normalize_mode(os.environ[ENV_MODE], origin=ENV_MODE)
    out = git(repo, "config", "--get", "inboundguard.mode", check=False).decode().strip()
    return normalize_mode(out or None, origin="git config inboundguard.mode")


def thresholds(policy: Dict, source: str) -> Dict[str, str]:
    merged = {"hold_at": policy.get("hold_at", "medium"), "block_at": policy.get("block_at", "critical")}
    override = policy.get("sources", {}).get(source, {})
    merged.update({k: v for k, v in override.items() if k in ("hold_at", "block_at")})
    return merged


def decide(findings: Iterable[Finding], policy: Dict, source: str) -> str:
    limits = thresholds(policy, source)
    hold = SEVERITY_RANK[limits["hold_at"]]
    block = SEVERITY_RANK[limits["block_at"]]
    worst = max((SEVERITY_RANK[f.severity] for f in findings), default=-1)
    if worst >= block:
        return "block"
    if worst >= hold:
        return "hold"
    return "allow"
