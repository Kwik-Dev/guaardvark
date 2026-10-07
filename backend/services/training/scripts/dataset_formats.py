"""Reading fine-tuning datasets: one reader for the backend and the trainer.

The Training page's inspect panel, the job checks in the backend and the
trainer subprocess all read datasets through this module, so what the panel
calls trainable is what the trainer trains on. Standard library only: the
trainer imports it before (and without) the backend package.

A dataset is a .jsonl or .json file, or a folder searched recursively for
them (hidden files and folders skipped). Each row is a JSON object in one of
these shapes, detected per row:

- ``messages``: {"messages": [{"role": "user"|"assistant"|"system", "content": ...}]}
- ``sharegpt``: {"conversations": [{"from": "human"|"gpt"|"system", "value": ...}]}
- ``prompt_completion``: {"prompt": ..., "completion": ...}
- ``alpaca``: {"instruction": ..., "input": optional, "output": ...}
- ``text``: {"text": ...}, trained as plain text without a chat template
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

SUFFIXES = (".jsonl", ".json")

# A folder with more files than this is almost certainly not a dataset (a
# home folder, a repository); refused rather than read for minutes.
MAX_FILES = 2000
# Errors kept per inspect; the count is always complete.
MAX_ERRORS = 20
# Characters of each message shown in an inspect sample.
SAMPLE_CHARS = 400

FORMATS = ("messages", "sharegpt", "prompt_completion", "alpaca", "text")

_SHAREGPT_ROLES = {
    "human": "user", "user": "user",
    "gpt": "assistant", "assistant": "assistant", "model": "assistant", "bot": "assistant",
    "system": "system",
}
_ROLES = ("system", "user", "assistant")


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def list_files(path: Any) -> tuple[list[str], str | None]:
    """The training files a dataset path names, or why it names none.

    A .jsonl or .json file is used as it is. A folder contributes every such
    file under it, recursively, in path order, skipping hidden files and
    folders. Returns (files, reason); files is empty exactly when reason is set.
    """
    if not isinstance(path, str):
        return [], "the dataset path must be text"
    raw = path.strip()
    if not raw:
        return [], "the dataset has no path"
    if "://" in raw:
        return [], f"its path is a URL ({raw}); training reads .jsonl or .json files on this machine"
    target = _expand(raw)
    if target.is_file():
        if target.suffix.lower() in SUFFIXES:
            return [str(target)], None
        return [], f"{target.name} is not a .jsonl or .json file"
    if target.is_dir():
        files: list[str] = []
        for root, dirs, names in os.walk(target):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                if name.startswith(".") or Path(name).suffix.lower() not in SUFFIXES:
                    continue
                files.append(os.path.join(root, name))
                if len(files) > MAX_FILES:
                    return [], (f"the folder {target} holds more than {MAX_FILES} .jsonl or .json "
                                f"files; choose the folder or file that holds the dataset")
        if files:
            return sorted(files), None
        return [], f"the folder {target} holds no .jsonl or .json file (parse its transcripts first)"
    return [], f"{target} does not exist"


def iter_rows(path: str, errors: list[str] | None = None) -> Iterator[dict]:
    """Yield the JSON objects in one dataset file.

    Blank lines are skipped. A line that is not JSON or not an object, and a
    .json file that is not a list of rows, is recorded in ``errors`` as
    "<file> line N: ..." and skipped; the file's text is never quoted.
    """
    for _, row in _located_rows(path, errors):
        yield row


def _located_rows(path: str, errors: list[str] | None) -> Iterator[tuple[str, dict]]:
    """iter_rows, with each row's place in its file ("line 3", "row 3")."""
    name = Path(path).name

    def fail(message: str) -> None:
        if errors is not None:
            errors.append(f"{name} {message}")

    if Path(path).suffix.lower() == ".json":
        try:
            with open(path, encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            fail(f"could not be read as JSON ({type(e).__name__})")
            return
        if not isinstance(records, list):
            fail("is not a list of rows (a .json dataset holds one JSON list)")
            return
        for index, record in enumerate(records, 1):
            if isinstance(record, dict):
                yield f"row {index}", record
            else:
                fail(f"row {index}: not a JSON object")
        return

    try:
        f = open(path, "rb")
    except OSError as e:
        fail(f"could not be opened ({type(e).__name__})")
        return
    with f:
        for number, raw in enumerate(f, 1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                fail(f"line {number}: not valid JSON")
                continue
            if isinstance(record, dict):
                yield f"line {number}", record
            else:
                fail(f"line {number}: not a JSON object")


def _text(value: Any) -> str | None:
    """A message's content as text: a string, or the text parts of a list."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [p.get("text") for p in value if isinstance(p, dict) and isinstance(p.get("text"), str)]
        if parts and len(parts) == len(value):
            return "".join(parts)
    return None


def detect_format(row: Any) -> str | None:
    """Which of FORMATS a row is in, or None."""
    if not isinstance(row, dict):
        return None
    if isinstance(row.get("messages"), list):
        return "messages"
    if isinstance(row.get("conversations"), list):
        return "sharegpt"
    if "prompt" in row and "completion" in row:
        return "prompt_completion"
    if "instruction" in row and "output" in row:
        return "alpaca"
    if isinstance(row.get("text"), str):
        return "text"
    return None


def _from_turns(turns: list, role_key: str, content_key: str, roles: dict | None) -> tuple[list | None, str | None]:
    messages = []
    for turn in turns:
        if not isinstance(turn, dict):
            return None, "a turn is not a JSON object"
        role = turn.get(role_key)
        role = roles.get(str(role).lower()) if roles is not None else role
        if role not in _ROLES:
            return None, f"unknown role {turn.get(role_key)!r}"
        content = _text(turn.get(content_key))
        if content is None:
            return None, f"a {role} turn has no text"
        messages.append({"role": role, "content": content})
    return messages, None


def _finish(messages: list) -> tuple[list | None, str | None]:
    """Drop trailing turns after the last answer; require an answer with text."""
    while messages and messages[-1]["role"] != "assistant":
        messages.pop()
    if not messages:
        return None, "no assistant turn"
    if not messages[-1]["content"].strip():
        return None, "the assistant turn is empty"
    if not any(m["role"] == "user" and m["content"].strip() for m in messages):
        return None, "no user turn"
    return messages, None


def to_messages(row: Any) -> tuple[list | None, str | None]:
    """A chat row as [{"role", "content"}], ending on an assistant turn.

    Returns (messages, None), or (None, reason) for a row that cannot be
    trained as a conversation. ``text`` rows have no turns and give a reason.
    """
    fmt = detect_format(row)
    if fmt == "messages":
        messages, reason = _from_turns(row["messages"], "role", "content", None)
    elif fmt == "sharegpt":
        messages, reason = _from_turns(row["conversations"], "from", "value", _SHAREGPT_ROLES)
    elif fmt == "prompt_completion":
        prompt, completion = row.get("prompt"), row.get("completion")
        if isinstance(prompt, list) and isinstance(completion, list):
            messages, reason = _from_turns(prompt + completion, "role", "content", None)
        elif isinstance(prompt, str) and isinstance(completion, str):
            messages, reason = [{"role": "user", "content": prompt},
                                {"role": "assistant", "content": completion}], None
        else:
            return None, "prompt and completion must both be text or both be lists of messages"
    elif fmt == "alpaca":
        instruction, extra, output = row.get("instruction"), row.get("input"), row.get("output")
        if not isinstance(instruction, str) or not isinstance(output, str):
            return None, "instruction and output must be text"
        if extra is not None and not isinstance(extra, str):
            return None, "input must be text"
        user = instruction if not (extra or "").strip() else f"{instruction}\n\n{extra}"
        messages = [{"role": "user", "content": user}, {"role": "assistant", "content": output}]
        system = row.get("system")
        if isinstance(system, str) and system.strip():
            messages.insert(0, {"role": "system", "content": system})
        reason = None
    elif fmt == "text":
        return None, "plain text rows have no conversation turns"
    else:
        return None, "unrecognised row (expected messages, conversations, prompt/completion, instruction/output or text)"
    if reason:
        return None, reason
    return _finish(messages)


def row_problem(row: Any) -> str | None:
    """Why a row cannot be trained on, or None when it can."""
    fmt = detect_format(row)
    if fmt == "text":
        return None if row["text"].strip() else "the text is empty"
    _, reason = to_messages(row)
    return reason


def _clip(text: str) -> str:
    return text if len(text) <= SAMPLE_CHARS else text[:SAMPLE_CHARS] + "…"


def _sample(row: dict, fmt: str) -> dict:
    if fmt == "text":
        return {"format": fmt, "text": _clip(row["text"])}
    messages, _ = to_messages(row)
    return {"format": fmt, "messages": [{"role": m["role"], "content": _clip(m["content"])} for m in messages]}


def inspect(path: Any, samples: int = 3) -> dict:
    """What a dataset path holds, for the inspect panel and save-time checks.

    Returns {"path", "kind", "files", "rows", "usable", "formats", "samples",
    "errors", "error_count", "trainable", "reason"}. ``rows`` counts JSON
    objects read, ``usable`` those the trainer can train on; ``errors`` names
    file and line, never the line's text. ``samples`` are the first usable
    rows, clipped, and are the only content returned.
    """
    result: dict[str, Any] = {
        "path": path if isinstance(path, str) else None, "kind": None, "files": [], "rows": 0,
        "usable": 0, "formats": {}, "samples": [], "errors": [], "error_count": 0,
        "trainable": False, "reason": None,
    }
    files, reason = list_files(path)
    if reason:
        result["reason"] = reason
        return result
    target = _expand(path.strip())
    result["path"] = str(target.resolve())
    result["kind"] = "folder" if target.is_dir() else "file"

    errors: list[str] = []
    for name in files:
        counts = {"path": name, "rows": 0, "usable": 0}
        for where, row in _located_rows(name, errors):
            counts["rows"] += 1
            problem = row_problem(row)
            if problem:
                errors.append(f"{Path(name).name} {where}: {problem}")
                continue
            counts["usable"] += 1
            fmt = detect_format(row)
            result["formats"][fmt] = result["formats"].get(fmt, 0) + 1
            if len(result["samples"]) < samples:
                result["samples"].append(_sample(row, fmt))
        result["files"].append(counts)
        result["rows"] += counts["rows"]
        result["usable"] += counts["usable"]

    result["error_count"] = len(errors)
    result["errors"] = errors[:MAX_ERRORS]
    result["trainable"] = result["usable"] > 0
    if not result["trainable"]:
        if result["rows"] == 0 and not errors:
            result["reason"] = "the dataset holds no rows"
        else:
            result["reason"] = (f"none of its {result['rows']} rows can be trained on"
                                + (f" (first problem: {errors[0]})" if errors else ""))
    return result
