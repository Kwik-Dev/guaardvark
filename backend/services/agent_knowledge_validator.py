#!/usr/bin/env python3
"""Validation helpers for agent recipes and screen-control knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


SUPPORTED_RECIPE_ACTIONS = {
    "click",
    "hotkey",
    "type",
    "wait",
    "wait_until_settled",
    "wait_until_visible",
}


# Safety bounds. A recipe runs before any model reads the request: a trigger
# match sends its keys and text straight to the agent's browser, which carries
# the person's logged-in sessions. A recipe that breaks one of these is an
# error, and the agent never loads it, wherever it came from (a pull request,
# a local edit, a promoted candidate).

# Hosts a type step may spell out in fixed text. Everything else a recipe
# types comes from the person's own request through a {n} capture.
RECIPE_URL_HOSTS = frozenset({"www.youtube.com", "youtube.com"})

# Keys that leave the browser for the system, or open a console that runs code
# in the page with the person's sessions.
DENIED_HOTKEYS = {
    frozenset({"ctrl", "alt", "t"}): "opens a terminal",
    frozenset({"alt", "f2"}): "opens a run-command dialog",
    frozenset({"ctrl", "alt", "delete"}): "reaches the session manager",
    frozenset({"ctrl", "alt", "backspace"}): "kills the display server",
    frozenset({"f12"}): "opens the browser's developer tools",
    frozenset({"ctrl", "shift", "i"}): "opens the browser's developer tools",
    frozenset({"ctrl", "shift", "c"}): "opens the browser's developer tools",
    frozenset({"ctrl", "shift", "j"}): "opens the browser console",
    frozenset({"ctrl", "shift", "k"}): "opens the browser console",
    frozenset({"ctrl", "shift", "e"}): "opens the browser's network tools",
}
_SYSTEM_KEYS = frozenset({"super", "super_l", "super_r", "meta", "meta_l", "meta_r",
                          "hyper", "hyper_l", "hyper_r", "win"})
_KEY_ALIASES = {"control": "ctrl", "control_l": "ctrl", "control_r": "ctrl",
                "alt_l": "alt", "alt_r": "alt", "shift_l": "shift", "shift_r": "shift",
                "del": "delete"}

# Click targets that spend, destroy or grant. Posting words are not here: the
# recipes that post are confined to their site by the agent at run time.
_DENIED_CLICK_WORDS = frozenset({"delete", "remove", "erase", "uninstall", "format",
                                 "pay", "purchase", "buy", "checkout", "transfer",
                                 "install", "allow", "grant", "authorize", "authorise"})

# Requests no recipe may claim. A trigger that matches one would hijack an
# everyday message before the model sees it.
EVERYDAY_REQUESTS = (
    "", "hi", "hello", "help", "yes", "no", "ok", "thanks", "continue", "stop", "do it",
    "what can you do", "what time is it", "tell me a joke", "write a poem",
    "summarize this document", "remember this", "make an image of a cat",
    "what is the weather today", "show me my files", "delete all my files",
    "check my email", "send an email to my boss", "open my bank account",
)


@dataclass
class ValidationIssue:
    path: str
    message: str
    severity: str = "error"


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def add(self, path: str, message: str, severity: str = "error") -> None:
        self.issues.append(ValidationIssue(path=path, message=message, severity=severity))

    def error_messages(self) -> List[str]:
        return [f"{i.path}: {i.message}" for i in self.issues if i.severity == "error"]


def _placeholders(text: str) -> Iterable[int]:
    for match in re.finditer(r"\{(\d+)\}", text or ""):
        yield int(match.group(1))


def _hotkey_issue(keys: Any) -> Optional[str]:
    """Why a hotkey step is refused, or None."""
    if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
        return "hotkey step needs a non-empty list of key names"
    names = frozenset(_KEY_ALIASES.get(k.strip().lower(), k.strip().lower()) for k in keys)
    if names & _SYSTEM_KEYS:
        return f"hotkey {'+'.join(keys)} uses the system key"
    if {"ctrl", "alt"} <= names and any(re.fullmatch(r"f([1-9]|1[0-2])", n) for n in names):
        return f"hotkey {'+'.join(keys)} switches to a console"
    why = DENIED_HOTKEYS.get(names)
    return f"hotkey {'+'.join(keys)} {why}" if why else None


def _type_text_issue(text: Any) -> Optional[str]:
    """Why a type step's text is refused, or None.

    Fixed text is allowed only as an address prefix: a bare scheme, or a URL on
    RECIPE_URL_HOSTS. Anything else must come from the request's {n} captures.
    """
    if not isinstance(text, str):
        return "type step needs text"
    literal = re.sub(r"\{\d+\}", "", text)
    if literal in ("", "http://", "https://"):
        return None
    # The host must end at a path, query or the end of the text: a capture
    # right after it ("youtube.com{1}") could extend it to another host.
    host = re.match(r"https?://([^/\s?#{}]+)(?:[/?#]|$)", text)
    if (host and host.group(1).split(":")[0].lower() in RECIPE_URL_HOSTS
            and not re.search(r"\s", literal)):
        return None
    return (f"types fixed text {literal[:40]!r}; a recipe types only what the request "
            f"supplies, or an address on {', '.join(sorted(RECIPE_URL_HOSTS))}")


def validate_recipe(name: str, recipe: Dict[str, Any], strict: bool = False) -> ValidationResult:
    """Validate one recipes.json entry against LEARNING_PRINCIPLES.md."""
    result = ValidationResult()
    if not isinstance(recipe, dict):
        result.add(name, "recipe must be an object")
        return result

    triggers = recipe.get("triggers") or []
    steps = recipe.get("steps") or []
    if not isinstance(triggers, list) or not triggers:
        result.add(f"{name}.triggers", "must be a non-empty list")
    if not isinstance(steps, list) or not steps:
        result.add(f"{name}.steps", "must be a non-empty list")

    max_capture_group = 0
    for idx, pattern in enumerate(triggers):
        path = f"{name}.triggers[{idx}]"
        if not isinstance(pattern, str):
            result.add(path, "trigger must be a string")
            continue
        if not pattern.startswith("^"):
            result.add(path, "trigger must be anchored with ^")
        if not pattern.endswith("$") and not pattern.endswith("\\s*$"):
            result.add(
                path,
                "trigger must be anchored at the end",
                severity="error" if strict else "warning",
            )
        try:
            compiled = re.compile(pattern)
            max_capture_group = max(max_capture_group, compiled.groups)
        except re.error as e:
            result.add(path, f"invalid regex: {e}")
            continue
        # The matcher searches case-insensitively; test the same way.
        claimed = next((r for r in EVERYDAY_REQUESTS
                        if re.search(pattern, r, re.IGNORECASE)), None)
        if claimed is not None:
            result.add(path, f"trigger claims the everyday request {claimed!r}")

    nontrivial_actions = 0
    for idx, step in enumerate(steps):
        path = f"{name}.steps[{idx}]"
        if not isinstance(step, dict):
            result.add(path, "step must be an object")
            continue
        action = step.get("action")
        if action not in SUPPORTED_RECIPE_ACTIONS:
            result.add(path, f"unsupported action {action!r}")
            continue
        if action != "wait":
            nontrivial_actions += 1

        if action == "click":
            if "x" in step or "y" in step:
                result.add(path, "click steps must not store x/y coordinates")
            target = (step.get("target_description") or "").strip()
            if not target:
                result.add(path, "click step needs target_description")
            words = re.findall(r"[A-Za-z0-9_]+", target)
            if len(words) > 6:
                result.add(path, "target_description must be <= 6 words")
            denied = sorted({w.lower() for w in words} & _DENIED_CLICK_WORDS)
            if denied:
                result.add(path, f"click target {target!r} spends, destroys or grants ({', '.join(denied)})")

        if action == "hotkey":
            why = _hotkey_issue(step.get("keys"))
            if why:
                result.add(path, why)

        if action == "type":
            why = _type_text_issue(step.get("text"))
            if why:
                result.add(path, why)

        if action == "wait":
            result.add(path, "legacy wait step should migrate to wait_until_*", severity="warning")

        for key, value in step.items():
            if isinstance(value, str):
                for group_idx in _placeholders(value):
                    if group_idx > max_capture_group:
                        result.add(path, f"placeholder {{{group_idx}}} has no matching trigger capture")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for group_idx in _placeholders(item):
                            if group_idx > max_capture_group:
                                result.add(path, f"placeholder {{{group_idx}}} has no matching trigger capture")

    if nontrivial_actions > 1 and not recipe.get("success_proof"):
        result.add(
            f"{name}.success_proof",
            "nontrivial recipes should declare a final vision-readable proof",
            severity="warning",
        )

    return result


def validate_recipe_library(recipes: Dict[str, Any], strict: bool = False) -> ValidationResult:
    result = ValidationResult()
    for name, recipe in (recipes or {}).items():
        if str(name).startswith("_"):
            continue
        child = validate_recipe(str(name), recipe, strict=strict)
        result.issues.extend(child.issues)
    return result
