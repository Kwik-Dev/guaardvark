"""Session-scoped CLI working memory.

This is deliberately short-lived context: active files and follow-up targets
belong to the chat session, not the agent's durable memory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_RECENT_FILES = 5

_RECOMMENDATION_RE = re.compile(
    r"\b(suggest|recommend|improve|improvements|review)\b", re.IGNORECASE
)
_IMPLEMENT_RE = re.compile(
    r"\b(implement|apply|make|do)\b.*\b(improvement|improvements|recommendation|recommendations|suggestion|suggestions)\b",
    re.IGNORECASE,
)
_DEICTIC_FILE_RE = re.compile(
    r"\b(the file|this file|that file|active file|those improvements|these improvements|those recommendations|that)\b",
    re.IGNORECASE,
)


def empty_working_memory() -> dict[str, Any]:
    return {
        "active_file": None,
        "recent_files": [],
        "last_recommendation": None,
        "pending_edit_target": None,
    }


def normalize_working_memory(value: Any) -> dict[str, Any]:
    memory = empty_working_memory()
    if isinstance(value, dict):
        for key in memory:
            if key in value:
                memory[key] = value[key]
    if not isinstance(memory.get("recent_files"), list):
        memory["recent_files"] = []
    return memory


def apply_attachments(memory: dict[str, Any], attachments: list[dict]) -> None:
    readable = [
        a for a in attachments
        if a.get("read_status") == "ok" and a.get("is_file") and a.get("path")
    ]
    if not readable:
        return

    active = readable[-1]
    memory["active_file"] = active["path"]

    recent = [a["path"] for a in readable]
    for existing in memory.get("recent_files", []):
        if existing not in recent:
            recent.append(existing)
    memory["recent_files"] = recent[:MAX_RECENT_FILES]


def apply_user_intent(memory: dict[str, Any], raw_message: str) -> None:
    active = memory.get("active_file")
    if not active:
        return

    if _RECOMMENDATION_RE.search(raw_message):
        memory["last_recommendation"] = {"file": active}
        if _DEICTIC_FILE_RE.search(raw_message):
            memory["pending_edit_target"] = active

    if _IMPLEMENT_RE.search(raw_message):
        recommendation = memory.get("last_recommendation") or {}
        memory["pending_edit_target"] = recommendation.get("file") or active


def record_recommendation_summary(memory: dict[str, Any], raw_message: str, assistant_text: str) -> None:
    active = memory.get("active_file")
    if not active or not _RECOMMENDATION_RE.search(raw_message):
        return

    summary = _compact_summary(assistant_text)
    recommendation = {"file": active}
    if summary:
        recommendation["summary"] = summary
    memory["last_recommendation"] = recommendation


def build_cli_context(runtime_context: str, memory: dict[str, Any]) -> str:
    blocks = []
    if runtime_context:
        blocks.append(runtime_context)

    active = memory.get("active_file")
    recent = memory.get("recent_files") or []
    recommendation = memory.get("last_recommendation") or {}
    edit_target = memory.get("pending_edit_target")

    lines = ["[CLI Working Context]"]
    if active:
        lines.append(f"Active file: {active}")
        lines.append("When the user says 'the file' or 'this file', resolve it to Active file unless they name another file.")
        lines.append("Do not substitute RAG snippets or unrelated previous files for active-file references.")
    if recent:
        lines.append("Recent files: " + ", ".join(recent[:MAX_RECENT_FILES]))
    if recommendation.get("file"):
        lines.append(f"Last recommendation target: {recommendation['file']}")
        if recommendation.get("summary"):
            lines.append(f"Last recommendation summary: {recommendation['summary']}")
        lines.append("When the user asks to implement those improvements, use the last recommendation target only.")
    if edit_target:
        lines.append(f"Expected edit target: {edit_target}")

    if len(lines) > 1:
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def should_demote_rag(raw_message: str, memory: dict[str, Any], attachments: list[dict] | None = None) -> bool:
    if attachments:
        return True
    if memory.get("active_file") and _DEICTIC_FILE_RE.search(raw_message):
        return True
    if memory.get("pending_edit_target") and _IMPLEMENT_RE.search(raw_message):
        return True
    return False


def expected_edit_target(memory: dict[str, Any]) -> str | None:
    return memory.get("pending_edit_target") or memory.get("active_file")


def extract_approval_targets(data: dict) -> list[str]:
    targets: list[str] = []
    details = data.get("tool_details") or data.get("tool_calls") or []
    for item in details:
        if not isinstance(item, dict):
            continue
        params = item.get("params") or item.get("arguments") or item.get("args") or {}
        if not isinstance(params, dict):
            continue
        path = params.get("filepath") or params.get("file_path") or params.get("path")
        if path:
            targets.append(str(path))
    return targets


def approval_target_mismatch(data: dict, expected: str | None) -> tuple[bool, list[str]]:
    if not expected:
        return False, []

    actual_targets = extract_approval_targets(data)
    if not actual_targets:
        return False, []

    editable_targets = [
        target for target in actual_targets
        if _tool_is_edit_like_for_target(data, target)
    ]
    if not editable_targets:
        return False, actual_targets

    return not all(_paths_match(expected, actual) for actual in editable_targets), actual_targets


def _tool_is_edit_like_for_target(data: dict, target: str) -> bool:
    details = data.get("tool_details") or data.get("tool_calls") or []
    if not details:
        return any(tool in {"edit_code", "generate_file"} for tool in data.get("tools", []))
    for item in details:
        if not isinstance(item, dict):
            continue
        params = item.get("params") or item.get("arguments") or item.get("args") or {}
        path = params.get("filepath") or params.get("file_path") or params.get("path") if isinstance(params, dict) else None
        if str(path) == target:
            return item.get("tool") in {"edit_code", "generate_file"}
    return False


def _paths_match(expected: str, actual: str) -> bool:
    exp = Path(expected).expanduser()
    act = Path(actual).expanduser()
    exp_norm = str(exp).replace("\\", "/").rstrip("/")
    act_norm = str(act).replace("\\", "/").rstrip("/")
    if not exp_norm or not act_norm:
        return False
    if exp_norm == act_norm:
        return True
    if not act.is_absolute() and exp_norm.endswith("/" + act_norm.lstrip("./")):
        return True
    return False


def _compact_summary(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    summary = " ".join(lines[:8])
    if len(summary) > 1200:
        summary = summary[:1197] + "..."
    return summary
