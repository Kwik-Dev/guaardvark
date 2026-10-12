"""Scrubbing notes before they become training data.

Always on for "Build from folder": key and token shapes, email addresses,
private network addresses and a per-install word list (names, machine names,
clients) are replaced by neutral placeholders before any text reaches the
pair generator. The report counts what was replaced, per rule and per file,
and keeps a few sample lines already scrubbed, so the report itself carries
nothing it was meant to remove.

The word list is local data, one term per line, in
``STORAGE_DIR/training/scrub_terms.txt``; it is never part of the repository.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable

SAMPLES_PER_RULE = 5
SAMPLE_WIDTH = 160

# A key block spans lines, so it is replaced across the whole text first.
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)

# The rest work line by line: (name, compiled pattern, placeholder). Order
# matters: the key rules run before the generic assignment rule, and emails
# before hosts. A quote around a value stays where it was.
_RULES = [
    ("api_key", re.compile(
        r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_\-]{35}"
        r"|xox[abprs]-[A-Za-z0-9\-]{10,}"
        r"|hf_[A-Za-z0-9]{20,})"), "[key]"),
    ("bearer_header", re.compile(r"(?i)\b(authorization:\s*bearer|bearer)\s+[A-Za-z0-9._~+/=\-]{12,}"), r"\1 [key]"),
    ("api_key_header", re.compile(r"(?i)\b(x-api-key\s*[:=]\s*['\"]?)[^\s'\"]{8,}"), r"\1[key]"),
    ("secret_assignment", re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*\s*[=:]\s*['\"]?)"
        r"[^\s'\"\[]{6,}"), r"\1[key]"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[email]"),
    ("private_ip", re.compile(
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
        r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}"
        r"|169\.254\.\d{1,3}\.\d{1,3})\b"), "[ip]"),
    ("lan_host", re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]*\.(?:local|lan|home\.arpa|internal)\b"), "[host]"),
]

RULE_NAMES = ["private_key"] + [name for name, _, _ in _RULES] + ["word_list"]


def load_terms(path: Path) -> list[str]:
    """The word list, one term per line; blank lines and # comments skipped."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [t.strip() for t in lines if t.strip() and not t.strip().startswith("#")]


def save_terms(path: Path, terms: Iterable[str]) -> list[str]:
    """Write the word list, de-duplicated (case-blind) and in the order given."""
    seen, kept = set(), []
    for term in terms:
        term = str(term).strip()
        if term and not term.startswith("#") and term.lower() not in seen:
            seen.add(term.lower())
            kept.append(term)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{t}\n" for t in kept), encoding="utf-8")
    return kept


def _terms_pattern(terms: list[str]):
    if not terms:
        return None
    # Longest first, so "Harbor Bakery" is replaced before "Harbor".
    alternatives = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?i)(?<![\w]){alternatives}(?![\w])")


class Scrubber:
    """Replaces what the rules and the word list match, and keeps the report."""

    def __init__(self, terms: list[str] | None = None):
        self._terms = _terms_pattern(list(terms or []))
        self.counts: Counter = Counter()
        self.by_file: dict[str, Counter] = {}
        self.samples: dict[str, list[str]] = {}

    def scrub(self, text: str, source: str = "") -> str:
        file_counts = self.by_file.setdefault(source, Counter())
        text, n = _PRIVATE_KEY.subn("[key]", text)
        if n:
            self._count("private_key", n, file_counts)
            self._keep_sample("private_key", "[key]")
        rules = list(_RULES)
        if self._terms is not None:
            rules.append(("word_list", self._terms, "[name]"))
        out = []
        for line in text.split("\n"):
            fired = []
            for name, pattern, placeholder in rules:
                line, n = pattern.subn(placeholder, line)
                if n:
                    self._count(name, n, file_counts)
                    fired.append(name)
            # Samples come from the line after every rule has run, so no rule's
            # sample shows what a later rule removes.
            for name in fired:
                self._keep_sample(name, line)
            out.append(line)
        return "\n".join(out)

    def _count(self, name: str, n: int, file_counts: Counter) -> None:
        self.counts[name] += n
        file_counts[name] += n

    def _keep_sample(self, name: str, line: str) -> None:
        samples = self.samples.setdefault(name, [])
        if len(samples) >= SAMPLES_PER_RULE:
            return
        at = max(0, line.find("["))
        start = max(0, at - SAMPLE_WIDTH // 2)
        sample = line[start:start + SAMPLE_WIDTH].strip()
        if sample and sample not in samples:
            samples.append(sample)

    def report(self) -> dict:
        return {
            "total": sum(self.counts.values()),
            "by_rule": {name: self.counts.get(name, 0) for name in RULE_NAMES},
            "by_file": {src: dict(c) for src, c in self.by_file.items() if c},
            "samples": {name: s for name, s in self.samples.items() if s},
        }
