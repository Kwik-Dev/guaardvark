"""Posture: the parts of an install that steer it without being code in the checkout.

The source watch reads files; these checks read the settings and inventories
around them, which change what Guaardvark does as surely as an edit would:

- git plumbing: remotes, url.*.insteadOf, core.hooksPath, signing, and the
  hook files themselves — the push guard and signed releases rest on these
- .env key names (never values): a new cloud API key or endpoint is a cloud
  path being switched on
- the agents' rules and lessons in the database
- skills linked into the user's agent skills folder, and where they point
- packages installed in the backend's environment
- pickle-format model files appearing in the model folders
- extension and plugin folders: code that loads in-process at the next boot

Each check keeps its last snapshot as a baseline row (path "@posture/<name>").
The first run records quietly; after that a change is diffed and judged, and
anything at or above the hold threshold waits in the review list, with
listeners told as for any verdict.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from backend.services import inbound_guard_service as guard

logger = logging.getLogger(__name__)

# A key whose name says it reaches a hosted service. Values are never read.
_CLOUD_KEY = re.compile(
    r"(OPENAI|ANTHROPIC|CLAUDE|MISTRAL|GEMINI|GOOGLE_API|VERTEX|COHERE|GROQ|TOGETHER|REPLICATE|PERPLEXITY|"
    r"DEEPSEEK|XAI|OPENROUTER|HUGGINGFACEHUB_API|SENTRY|POSTHOG|DATADOG|NEW_RELIC|AWS_|AZURE_|"
    r"_API_BASE|_BASE_URL|_ENDPOINT)", re.I)


def _git(*args: str) -> str:
    guard.engine()
    from scripts.inbound_guard.sources import git

    return git(guard.REPO_ROOT, *args, check=False).decode("utf-8", "replace")


# A remote URL can carry a credential in its user part; it is masked before it
# is stored or shown, so a change to it reads as "changed" without its value.
_URL_CREDENTIAL = re.compile(r"(://)([^/@\s]+)@")


def _mask(value: str) -> str:
    return _URL_CREDENTIAL.sub(lambda m: m.group(1) + m.group(2).split(":", 1)[0][:3] + "***@", value)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


# -- snapshots ---------------------------------------------------------------------------------

def snap_git() -> Dict[str, str]:
    out: Dict[str, str] = {}
    pattern = r"^(remote\..*\.(url|pushurl)|url\..*\.(insteadof|pushinsteadof)|core\.hookspath|commit\.gpgsign|tag\.gpgsign|gpg\..*|user\.signingkey)$"
    for line in _git("config", "--get-regexp", pattern).splitlines():
        key, _, value = line.partition(" ")
        out[f"config {key}"] = _mask(value)
    hooks = Path(_git("rev-parse", "--path-format=absolute", "--git-path", "hooks").strip() or ".")
    if hooks.is_dir():
        for hook in sorted(hooks.iterdir()):
            if hook.name.endswith(".sample") or hook.is_dir():
                continue
            target = os.readlink(hook) if hook.is_symlink() else ""
            try:
                content = _sha(hook.read_bytes())
            except OSError:
                content = "unreadable"
            out[f"hook {hook.name}"] = f"{target} {content}".strip()
    return out


def snap_env() -> Dict[str, str]:
    env = guard.REPO_ROOT / ".env"
    out: Dict[str, str] = {}
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name = line.split("=", 1)[0].replace("export ", "").strip()
            if name:
                out[name] = "set"
    return out


def snap_instructions() -> Dict[str, str]:
    from backend.models import Rule, db

    out: Dict[str, str] = {}
    for rule in db.session.query(Rule).all():
        label = rule.name or rule.command_label or f"#{rule.id}"
        out[f"rule {rule.id} {label}"[:200]] = _sha(
            f"{rule.rule_text}\0{rule.is_active}\0{rule.level}\0{rule.type}".encode("utf-8", "replace"))
    return out


def snap_skill_links() -> Dict[str, str]:
    folder = Path.home() / ".claude" / "skills"
    out: Dict[str, str] = {}
    if folder.is_dir():
        for entry in sorted(folder.iterdir()):
            out[entry.name] = os.readlink(entry) if entry.is_symlink() else "folder"
    return out


def snap_packages() -> Dict[str, str]:
    from importlib import metadata

    out: Dict[str, str] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").lower()
        if name:
            out[name] = dist.version
    return out


def snap_models() -> Dict[str, str]:
    data = guard.rules().data.get("posture", {})
    suffixes = tuple(data.get("pickle_suffixes", [".ckpt", ".pt", ".pth", ".bin", ".pkl", ".pickle"]))
    out: Dict[str, str] = {}
    for folder in data.get("model_dirs", []):
        root = guard.REPO_ROOT / folder
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.endswith(suffixes):
                    path = Path(dirpath) / name
                    try:
                        out[path.relative_to(guard.REPO_ROOT).as_posix()] = str(path.stat().st_size)
                    except (OSError, ValueError):
                        continue
    return out


def snap_code_folders() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for parent in ("extensions", "plugins"):
        root = guard.REPO_ROOT / parent
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and not entry.name.startswith((".", "_")):
                    out[f"{parent}/{entry.name}"] = os.readlink(entry) if entry.is_symlink() else "folder"
    return out


# -- judging a change ---------------------------------------------------------------------------

def _judge_git(key: str, change: str) -> Tuple[str, str]:
    if key.startswith("hook "):
        return "high", "a git hook changed; hooks run on every commit, merge and push"
    if "insteadof" in key or "hookspath" in key or ".url" in key or "pushurl" in key:
        return "high", "where git fetches from, pushes to, or which hooks it runs changed"
    return "medium", "commit or tag signing settings changed"


def _judge_env(key: str, change: str) -> Tuple[str, str]:
    if change != "removed" and _CLOUD_KEY.search(key):
        return "high", "a key that reaches a hosted service appeared in .env; a cloud path needs the operator's explicit approval"
    return "info", f".env key {change}"


def _judge_instructions(key: str, change: str) -> Tuple[str, str]:
    return "medium", f"an agent rule or lesson was {change}; it changes what agents are told"


def _judge_skill(key: str, change: str) -> Tuple[str, str]:
    return "medium", f"an agent skill link was {change}; skills are read as instructions"


def _judge_package(key: str, change: str) -> Tuple[str, str]:
    if change == "added":
        return "medium", "a package was installed into the backend's environment"
    return "low", f"a package was {change}"


def _judge_model(key: str, change: str) -> Tuple[str, str]:
    if change == "removed":
        return "info", "a pickle-format model file was removed"
    return "medium", "a pickle-format model file appeared or changed; loading it can run code, prefer .safetensors"


def _judge_folder(key: str, change: str) -> Tuple[str, str]:
    if change == "removed":
        return "info", "a code folder was removed"
    return "medium", f"a code folder was {change}; its code loads in-process at the next start"


CHECKS: List[Tuple[str, Callable[[], Dict[str, str]], Callable[[str, str], Tuple[str, str]]]] = [
    ("git", snap_git, _judge_git),
    ("env", snap_env, _judge_env),
    ("instructions", snap_instructions, _judge_instructions),
    ("skill-links", snap_skill_links, _judge_skill),
    ("packages", snap_packages, _judge_package),
    ("models", snap_models, _judge_model),
    ("code-folders", snap_code_folders, _judge_folder),
]


def _diff(before: Dict[str, str], after: Dict[str, str]) -> List[Tuple[str, str]]:
    changes = [(k, "added") for k in after if k not in before]
    changes += [(k, "removed") for k in before if k not in after]
    changes += [(k, "changed") for k in after if k in before and before[k] != after[k]]
    return sorted(changes)


def run() -> Dict[str, int]:
    """Snapshot every posture check against its baseline. Needs an app context."""
    from backend.models import InboundBaseline, db

    engine = guard.engine()
    rules = guard.rules()
    summary: Dict[str, int] = {}
    for name, snapshot, judge in CHECKS:
        try:
            current = snapshot()
        except Exception as exc:
            logger.warning("posture check %s failed: %s", name, exc)
            continue
        path = f"@posture/{name}"
        row = db.session.query(InboundBaseline).filter_by(path=path).first()
        digest = _sha(json.dumps(current, sort_keys=True).encode())
        if row is None:
            db.session.add(InboundBaseline(path=path, sha256=digest, status="clean", attribution="seed",
                                           accepted=json.dumps(current, sort_keys=True)))
            continue
        if row.sha256 == digest:
            continue
        before = json.loads(row.accepted or "{}")
        findings = []
        for key, change in _diff(before, current):
            severity, why = judge(key, change)
            excerpt = key if name in ("env", "instructions", "packages") else f"{key}: {current.get(key, before.get(key, ''))}"
            findings.append(engine.Finding(f"posture.{name}", "posture", severity, path, None,
                                           engine.rules.visible(excerpt), why))
        row.sha256 = digest
        row.accepted = json.dumps(current, sort_keys=True)
        row.attribution = "posture"
        summary[name] = len(findings)
        if not findings:
            continue
        verdict = engine.scan([], source="posture", subject=f"{name} changed", mode=guard.get_mode())
        verdict.findings = sorted(findings, key=lambda f: -engine.SEVERITY_RANK[f.severity])
        verdict.verdict = engine.policy.decide(verdict.findings, rules.policy, "posture")
        scan_id = guard.record(verdict)
        guard._emit({"kind": "verdict", "verdict": verdict.to_dict(), "scan_id": scan_id})
    db.session.commit()
    return summary
