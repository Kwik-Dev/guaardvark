"""Evaluate a list of Changes against the rule data in ``inbound_rules.json``.

Simple rules (a path, a pattern on a line) live entirely in the JSON. The checks
below need more than one regex — a host that must be classified, an identifier
that must be tokenized, a dependency name compared with the line it replaced —
so they are code, but their thresholds and lists still come from the JSON.
"""
from __future__ import annotations

import ast
import fnmatch
import io
import ipaddress
import json
import re
import time
import tokenize
import unicodedata
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import SEVERITY_RANK, Change, Finding

RULES_FILE = Path(__file__).with_name("inbound_rules.json")
EGRESS_FILE = Path(__file__).with_name("egress.json")

# (change, "old" | "new") -> full text, or None when it cannot be read
Loader = Callable[..., Optional[str]]

# Characters that reorder or hide text (CVE-2021-42574 "Trojan Source" and its
# invisible cousins). Reordering controls are refused outright; invisible ones are
# held, because a zero-width joiner inside an emoji in a UI string is legitimate.
BIDI_CONTROLS = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
INVISIBLE = set("\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e")

_URL = re.compile(r"\b(?:https?|wss?|ftp)://([^\s/'\"`<>()\[\]{}\\,;|]+)", re.I)
_BLOB = re.compile(r"[A-Za-z0-9+/_-]{%d,}={0,2}")
_HEX_ESCAPES = re.compile(r"(?:\\x[0-9a-fA-F]{2}){24,}")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_NPM_DEP = re.compile(r'^\s*"(@?[\w.-]+(?:/[\w.-]+)?)"\s*:\s*"([^"]*)"')
_NPM_VERSIONISH = re.compile(r"^(?:[\^~<>=]*\s*v?\d|npm:|workspace:|file:|link:|git|https?:|latest$|\*$|x$)")
_NPM_NOT_DEPS = {"version", "node", "npm", "yarn", "pnpm", "name", "license"}

_COMMENT_PREFIXES = {
    ".py": ("#",), ".sh": ("#",), ".bash": ("#",), ".yml": ("#",), ".yaml": ("#",),
    ".toml": ("#",), ".txt": ("#",), ".cfg": ("#",),
    ".js": ("//", "/*", "*"), ".jsx": ("//", "/*", "*"), ".mjs": ("//", "/*", "*"),
    ".cjs": ("//", "/*", "*"), ".ts": ("//", "/*", "*"), ".tsx": ("//", "/*", "*"),
}


def visible(text: str, limit: int = 160) -> str:
    """Make text safe to print: control and format characters shown as escapes.

    An excerpt holding a bidi override would otherwise reorder the very line
    that reports it.
    """
    out = []
    for ch in text.strip():
        if ch in BIDI_CONTROLS or ch in INVISIBLE or unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    s = "".join(out)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class RuleSet:
    def __init__(self, data: dict):
        self.data = data
        self.version = data.get("version", 1)
        self.policy = data.get("policy", {})
        self.caps = data.get("caps", {})
        self.groups: Dict[str, List[str]] = data.get("groups", {})
        self.path_rules = data.get("path_rules", [])
        self.line_rules = []
        for rule in data.get("line_rules", []):
            compiled = dict(rule)
            compiled["_re"] = re.compile(rule["pattern"])
            compiled["_unless"] = re.compile(rule["unless"]) if rule.get("unless") else None
            self.line_rules.append(compiled)
        net = data.get("network", {})
        self.approved_hosts = [h.lower() for h in net.get("approved_hosts", [])]
        self.local_hosts = {h.lower() for h in net.get("local_hosts", [])}
        self.local_suffixes = tuple(s.lower() for s in net.get("local_suffixes", []))
        self.protected_lists = data.get("protected_lists", [])
        # host pattern -> title of the outbound path that declares it
        self.declared_hosts: Dict[str, str] = {}
        for path in data.get("_egress", {}).get("paths", []):
            for host in path.get("hosts", []):
                self.declared_hosts.setdefault(host.lower(), path.get("title", path.get("id", "")))
        self._blob = re.compile(_BLOB.pattern % int(self.caps.get("blob_min_length", 120)))
        self._glob_cache: Dict[str, List[str]] = {}

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "RuleSet":
        with open(path or RULES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        egress = (Path(path).with_name("egress.json") if path else EGRESS_FILE)
        if egress.is_file():
            with open(egress, encoding="utf-8") as fh:
                data["_egress"] = json.load(fh)
        return cls(data)

    def declared_for(self, host: str) -> Optional[str]:
        """The outbound path (egress.json) that declares ``host``, if any."""
        bare = host.lower().rstrip(".").rsplit("@", 1)[-1].split(":", 1)[0]
        for pattern, title in self.declared_hosts.items():
            if fnmatch.fnmatchcase(bare, pattern):
                return title
        return None

    # -- path matching -----------------------------------------------------

    def _expand(self, names: Sequence[str]) -> List[str]:
        key = "\0".join(names)
        if key not in self._glob_cache:
            out: List[str] = []
            seen: Set[str] = set()

            def walk(items: Iterable[str]) -> None:
                for item in items:
                    if item.startswith("@"):
                        if item in seen:
                            continue
                        seen.add(item)
                        walk(self.groups.get(item[1:], []))
                    else:
                        out.append(item)

            walk(names)
            self._glob_cache[key] = out
        return self._glob_cache[key]

    def matches(self, path: str, names: Sequence[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, g) for g in self._expand(names))

    def in_group(self, path: str, group: str) -> bool:
        return self.matches(path, ["@" + group])

    # -- evaluation --------------------------------------------------------

    def evaluate(
        self,
        changes: Sequence[Change],
        *,
        blob_loader: Optional[Loader] = None,
        ignored: Optional[Set[str]] = None,
        deadline: Optional[float] = None,
        source: Optional[str] = None,
    ) -> Tuple[List[Finding], List[str]]:
        """Return (findings, notes). Notes say what was not read and why.

        A rule with a "sources" list applies only when the scan's source is in it.
        """
        collector = _Collector(int(self.caps.get("findings_per_rule_per_file", 5)))
        notes: List[str] = []
        for index, change in enumerate(changes):
            if deadline is not None and time.monotonic() > deadline:
                rest = len(changes) - index
                notes.append(f"time budget reached; {rest} file(s) not read — run scripts/check_inbound.py on this range")
                collector.add(Finding("scan.incomplete", "scan", "medium", changes[index].path, None, "",
                                      f"scan stopped at its time budget with {rest} file(s) unread"))
                break
            self._path_rules(change, collector, source)
            self._shape(change, ignored or set(), collector)
            self._protected_lists(change, collector, blob_loader)
            if change.binary:
                continue
            self._line_rules(change, collector, source)
            self._urls(change, collector, blob_loader)
            self._trojan(change, collector, blob_loader)
            self._new_dependencies(change, collector)
        cut = [c.path for c in changes if c.truncated]
        if cut:
            notes.append(f"added-line cap reached; {len(cut)} file(s) only partly read")
            collector.add(Finding("scan.incomplete", "scan", "medium", cut[0], None, "",
                                  f"added-line cap reached: {len(cut)} file(s) from here on were only partly "
                                  "read; scan a smaller range"))
        return collector.findings(), notes

    def _path_rules(self, change: Change, out: "_Collector", source: Optional[str] = None) -> None:
        for rule in self.path_rules:
            if rule.get("sources") and source not in rule["sources"]:
                continue
            if rule.get("status") and change.status not in rule["status"]:
                continue
            paths = [change.path] + ([change.old_path] if change.old_path else [])
            if any(self.matches(p, rule["paths"]) for p in paths):
                out.add(Finding(rule["id"], rule["pack"], rule["severity"], change.path, None,
                                f"{_STATUS.get(change.status, change.status)} file", rule["why"]))

    def _line_rules(self, change: Change, out: "_Collector", source: Optional[str] = None) -> None:
        prefixes = _comment_prefixes(change.path)
        for rule in self.line_rules:
            if rule.get("sources") and source not in rule["sources"]:
                continue
            if not self.matches(change.path, rule["paths"]):
                continue
            if rule.get("skip") and self.matches(change.path, rule["skip"]):
                continue
            side = rule.get("on", "added")
            if side in ("added", "both"):
                for number, text in change.added:
                    if _is_comment(text, prefixes):
                        continue
                    if rule["_re"].search(text) and not (rule["_unless"] and rule["_unless"].search(text)):
                        out.add(Finding(rule["id"], rule["pack"], rule["severity"], change.path, number,
                                        visible(text), rule["why"]))
            if side in ("removed", "both"):
                for text in change.removed:
                    if rule["_re"].search(text):
                        out.add(Finding(rule["id"], rule["pack"], rule["severity"], change.path, None,
                                        "removed: " + visible(text), rule["why"]))

    def _protected_lists(self, change: Change, out: "_Collector", blob_loader: Optional[Loader]) -> None:
        """Compare literal guard lists before and after, entry by entry.

        A diff with no context cannot say which list a removed line belonged to,
        so the lists are read whole from both versions of the file.
        """
        entries = [e for e in self.protected_lists if self.matches(change.path, e["paths"])]
        if not entries or change.status == "A":
            return
        if blob_loader is None:
            out.add(Finding("guard.protected-list", "guard-integrity", "high", change.path, None, "",
                            "a file holding guard lists changed and could not be compared"))
            return
        before = _literal_lists(blob_loader(change, "old"))
        after = _literal_lists(blob_loader(change, "new")) if change.status != "D" else {}
        for entry in entries:
            weakens_on = entry.get("weakens_on", "remove")
            for name in entry["names"]:
                if name not in before:
                    continue
                if name not in after:
                    out.add(Finding("guard.protected-list", "guard-integrity", "critical", change.path, None,
                                    name, f"{name} is gone or no longer a plain list"))
                    continue
                removed = sorted(before[name] - after[name])
                added = sorted(after[name] - before[name])
                weakened, other = (removed, added) if weakens_on == "remove" else (added, removed)
                for item in weakened:
                    verb = "removes" if weakens_on == "remove" else "adds"
                    out.add(Finding("guard.protected-list", "guard-integrity", "critical", change.path, None,
                                    visible(item), f"{verb} {visible(item, 80)!r} {'from' if verb == 'removes' else 'to'} "
                                    f"{name}, which weakens it"))
                for item in other:
                    out.add(Finding("guard.protected-list-tightened", "guard-integrity", "info", change.path, None,
                                    visible(item), f"{name} changed in the stricter direction"))

    # -- outbound network ---------------------------------------------------

    def host_is_local_or_approved(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        if "@" in host:
            host = host.rsplit("@", 1)[1]
        bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        if bare.count(":") == 1:
            bare = bare.split(":", 1)[0]
        if not bare or "." not in bare and ":" not in bare:
            return True  # single-label names never leave the LAN
        if any(ch in bare for ch in "{}<>$%*…") or "your" in bare or bare.startswith(("host", "server")):
            return True  # a placeholder, not a destination
        try:
            return not ipaddress.ip_address(bare).is_global
        except ValueError:
            pass
        if bare in self.local_hosts or bare.endswith(self.local_suffixes):
            return True
        return any(fnmatch.fnmatchcase(bare, pattern) for pattern in self.approved_hosts)

    def _urls(self, change: Change, out: "_Collector",
              blob_loader: Optional[Loader]) -> None:
        if not self.in_group(change.path, "code") or self.in_group(change.path, "tests"):
            return
        prefixes = _comment_prefixes(change.path)
        prose: Optional[Set[int]] = None
        for number, text in change.added:
            if _is_comment(text, prefixes):
                continue
            for match in _URL.finditer(text):
                host = match.group(1)
                if self.host_is_local_or_approved(host):
                    continue
                if change.path.endswith(".py") and blob_loader is not None:
                    if prose is None:
                        prose = _python_prose_lines(blob_loader(change))
                    if number in prose:
                        continue  # a docstring names the host; nothing here calls it
                declared = self.declared_for(host)
                if declared:
                    out.add(Finding("net.new-destination", "outbound-network", "medium", change.path, number,
                                    visible(text),
                                    f"names {visible(host, 60)}, which egress.json declares for "
                                    f"'{visible(declared, 80)}'; read what this new code sends there"))
                else:
                    out.add(Finding("net.new-destination", "outbound-network", "high", change.path, number,
                                    visible(text),
                                    f"names an outside host ({visible(host, 60)}) that no outbound path in "
                                    "scripts/inbound_guard/egress.json declares; the product never contacts one "
                                    "except behind a visible Install"))

    # -- trojan source and obfuscation ---------------------------------------

    def _trojan(self, change: Change, out: "_Collector",
                blob_loader: Optional[Loader]) -> None:
        is_code = self.in_group(change.path, "code")
        is_test = self.in_group(change.path, "tests")
        minified = int(self.caps.get("minified_line_length", 1000))
        non_ascii_py = False
        for number, text in change.added:
            chars = set(text)
            if chars & BIDI_CONTROLS:
                out.add(Finding("trojan.bidi", "trojan-source", "critical", change.path, number, visible(text),
                                "holds a bidirectional control character, which makes code read differently "
                                "from how it runs"))
            if is_code and chars & INVISIBLE:
                out.add(Finding("trojan.invisible", "trojan-source", "high", change.path, number, visible(text),
                                "holds an invisible character"))
            if not is_code or is_test:
                continue
            if change.path.endswith(".py") and any(ord(ch) > 127 for ch in text):
                non_ascii_py = True
            if _HEX_ESCAPES.search(text) or _has_mixed_blob(self._blob, text):
                out.add(Finding("trojan.encoded-blob", "trojan-source", "high", change.path, number, visible(text),
                                "carries a long encoded string in source; decode it and read what it is"))
            if len(text) > minified:
                out.add(Finding("trojan.minified", "trojan-source", "medium", change.path, number, visible(text),
                                f"a {len(text)}-character line in source, likely minified or generated"))
        if non_ascii_py and blob_loader is not None:
            self._python_identifiers(change, out, blob_loader)

    def _python_identifiers(self, change: Change, out: "_Collector",
                            blob_loader: Loader) -> None:
        text = blob_loader(change)
        if not text:
            return
        added = {n for n, _ in change.added}
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return
        for tok in tokens:
            if tok.type == tokenize.NAME and tok.start[0] in added and any(ord(ch) > 127 for ch in tok.string):
                out.add(Finding("trojan.identifier", "trojan-source", "high", change.path, tok.start[0],
                                visible(tok.line), f"identifier {visible(tok.string, 40)!s} uses non-ASCII letters, "
                                "which can impersonate a different name"))

    # -- dependencies ----------------------------------------------------------

    def _new_dependencies(self, change: Change, out: "_Collector") -> None:
        if self.in_group(change.path, "python_deps"):
            before = {_req_name(t) for t in change.removed} - {None}
            for number, text in change.added:
                name = _req_name(text)
                if not name or name in before:
                    continue
                spec = _REQ_NAME.match(text).group(3)
                pinned = "==" in spec
                out.add(Finding("supply.new-dependency", "supply-chain", "medium", change.path, number, visible(text),
                                f"adds the Python package {name}" + ("" if pinned else " without an exact pin")))
        elif self.in_group(change.path, "npm_manifest"):
            before = set()
            for text in change.removed:
                m = _NPM_DEP.match(text)
                if m:
                    before.add(m.group(1))
            for number, text in change.added:
                m = _NPM_DEP.match(text)
                if not m or m.group(1) in _NPM_NOT_DEPS or not _NPM_VERSIONISH.match(m.group(2).strip()):
                    continue
                if m.group(1) in before:
                    continue
                out.add(Finding("supply.new-dependency", "supply-chain", "medium", change.path, number, visible(text),
                                f"adds the npm package {m.group(1)}"))

    # -- file shape --------------------------------------------------------------

    def _shape(self, change: Change, ignored: Set[str], out: "_Collector") -> None:
        path = change.path
        if path == ".git" or path.startswith(".git/") or "/.git/" in path:
            out.add(Finding("shape.git-dir", "file-shape", "critical", path, None, "",
                            "writes inside a .git directory, where hooks and config live"))
        if change.status == "D":
            return
        if change.is_symlink:
            target = change.symlink_target or ""
            escapes = target.startswith("/") or _escapes_repo(path, target)
            out.add(Finding("shape.symlink", "file-shape", "high" if escapes else "medium", path, None,
                            visible(f"-> {target}"),
                            "a symbolic link" + (" that leaves the repository" if escapes else "")))
        if change.new_mode == "100755" and change.old_mode != "100755" and not self.in_group(path, "shell"):
            out.add(Finding("shape.executable", "file-shape", "low", path, None, "",
                            "made executable"))
        if self.in_group(path, "pickles"):
            out.add(Finding("shape.pickle-file", "file-shape", "high", path, None, "",
                            "a pickle-format file; loading it can run code"))
        elif change.binary and self.in_group(path, "code_dirs") and not self.in_group(path, "media"):
            out.add(Finding("shape.binary", "file-shape", "medium", path, None, "",
                            "a binary file among source; nothing in it can be read in review"))
        if change.status in ("A", "R", "C") and path in ignored:
            out.add(Finding("shape.ignored-path", "file-shape", "high", path, None, "",
                            "this clone keeps that path out of git; an incoming file there replaces the local one"))


class _Collector:
    """Keeps output bounded: a few examples per rule per file, then a count."""

    def __init__(self, per_rule_file: int):
        self.limit = per_rule_file
        self._items: List[Finding] = []
        self._counts: Dict[Tuple[str, str], int] = {}

    def add(self, finding: Finding) -> None:
        key = (finding.rule, finding.path)
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] <= self.limit:
            self._items.append(finding)

    def findings(self) -> List[Finding]:
        out = list(self._items)
        for (rule, path), count in self._counts.items():
            if count > self.limit:
                first = next(f for f in self._items if f.rule == rule and f.path == path)
                out.append(Finding(rule, first.pack, first.severity, path, None, "",
                                   f"and {count - self.limit} more like this in the same file"))
        out.sort(key=lambda f: (-SEVERITY_RANK[f.severity], f.path, f.line or 0))
        return out


_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied", "T": "type changed"}


def _comment_prefixes(path: str) -> Tuple[str, ...]:
    name = path.rsplit("/", 1)[-1]
    if "." not in name.lstrip("."):
        return ("#",)  # extensionless files here are shell hooks and Dockerfiles
    return _COMMENT_PREFIXES.get("." + name.rsplit(".", 1)[-1].lower(), ())


def _is_comment(text: str, prefixes: Tuple[str, ...]) -> bool:
    stripped = text.lstrip()
    return bool(prefixes) and stripped.startswith(prefixes)


def _literal_lists(text: Optional[str]) -> Dict[str, Set[str]]:
    """Top-level NAME = [ "a", "b" ] (list, tuple or set of strings) in a Python file."""
    if not text:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    found: Dict[str, Set[str]] = {}
    for node in tree.body:
        targets: List[ast.expr] = []
        value = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") in ("frozenset", "set", "tuple", "list") \
                and value.args:
            value = value.args[0]
        if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            continue
        items = [e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(items) != len(value.elts):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = set(items)
    return found


def _python_prose_lines(text: Optional[str]) -> Set[int]:
    """Lines covered by docstrings and other bare string statements."""
    lines: Set[int] = set()
    if not text:
        return lines
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return lines
    statement_start = {tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.NL}
    significant = [t for t in tokens if t.type not in (tokenize.COMMENT, tokenize.NL)]
    for i, tok in enumerate(significant):
        if tok.type != tokenize.STRING:
            continue
        before = significant[i - 1].type if i else None
        after = significant[i + 1].type if i + 1 < len(significant) else None
        if (before is None or before in statement_start) and after in (tokenize.NEWLINE, tokenize.ENDMARKER):
            lines.update(range(tok.start[0], tok.end[0] + 1))
    return lines


def _has_mixed_blob(pattern: "re.Pattern[str]", text: str) -> bool:
    for match in pattern.finditer(text):
        run = match.group(0)
        if any(c.isdigit() for c in run) and any(c.isupper() for c in run) and any(c.islower() for c in run):
            return True
    return False


def _req_name(text: str) -> Optional[str]:
    stripped = text.strip()
    if not stripped or stripped.startswith(("#", "-")):
        return None
    if "://" in stripped and "@" not in stripped.split("://", 1)[0]:
        return None  # a bare URL or VCS requirement; supply.vcs-dependency reports it
    match = _REQ_NAME.match(stripped)
    return match.group(1).lower().replace("_", "-") if match else None


def _escapes_repo(path: str, target: str) -> bool:
    depth = len(path.split("/")) - 1
    for part in target.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part and part != ".":
            depth += 1
    return False
