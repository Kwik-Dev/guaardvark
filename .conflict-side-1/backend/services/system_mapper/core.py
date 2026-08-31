"""SystemMap data model + codebase_map() orchestrator.

The SystemMap is the canonical output: three sub-graphs (dependency, reachability,
tool) plus a flat list of Findings that downstream consumers (self-improvement,
LLM agent context, human readers) can iterate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    HIGH = "high"      # broken/colliding code reachable in production
    MEDIUM = "medium"  # brittleness — works today, fragile under refactor
    LOW = "low"        # hygiene — dormant code, missing tests
    INFO = "info"      # observation, not a defect


class FindingKind(str, Enum):
    URL_PATH_COLLISION = "url-path-collision"
    URL_PREFIX_COLLISION = "url-prefix-collision"
    GHOST_ENDPOINT = "ghost-endpoint"          # backend route, no frontend caller
    GHOST_API_CALLER = "ghost-api-caller"      # frontend fetch, no backend route
    IMPORT_CYCLE = "import-cycle"
    OVER_COUPLED = "over-coupled"              # module appears in many cycles
    UNWIRED_TOOL = "unwired-tool"              # registered, not in CORE_TOOLS
    UNREGISTERED_TOOL = "unregistered-tool"    # in CORE_TOOLS, not registered
    UNTESTED_MODULE = "untested-module"
    DORMANT_MODULE = "dormant-module"          # no static importers
    BACKUP_ARTIFACT = "backup-artifact"        # .BACK / __BACKUP / _BACK files
    DEAD_SYMBOL = "dead-symbol"                # function defined, statically referenced nowhere
    # B4: liveness-consensus kinds. NONE of these are dispatchable (see
    # actions.DISPATCHABLE_KINDS) — a tracing window that misses a once-a-month
    # handler must never auto-delete it. They are advisory drift signals only.
    RUNTIME_ZOMBIE = "runtime-zombie"          # statically reachable but not fired in N days
    HOT_PATH_SPIKE = "hot-path-spike"          # unusually high recent hit rate
    CONTEXTUAL_DISCOVERY = "contextual-discovery"  # fired at runtime but no static importer


@dataclass
class Finding:
    """One actionable observation. Consumed by self-improvement, surfaced to LLM."""
    kind: FindingKind
    severity: Severity
    summary: str                       # one-line human description
    paths: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable id across re-runs: same defect → same id, so dismissals and
        dispatched-task state survive re-analysis. Independent of severity (which
        may be re-tuned) and of path ordering."""
        import hashlib
        basis = "|".join([self.kind.value, self.summary, *sorted(self.paths)])
        return hashlib.sha1(basis.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["severity"] = self.severity.value
        d["id"] = self.fingerprint()
        return d


@dataclass
class SystemMap:
    """Complete x-ray of a codebase at one point in time."""
    root: str
    generated_at: float
    languages: list[str]                                 # ['python', 'javascript']
    file_count: int                                       # source files surveyed

    # Sub-graphs (each module is responsible for its own data shape)
    dependency_graph: dict = field(default_factory=dict)
    node_meta: dict = field(default_factory=dict)   # module -> {lifecycle, importers, path}
    reachability: dict = field(default_factory=dict)
    tool_graph: dict = field(default_factory=dict)

    # Flat findings — the bridge to self-improvement
    findings: list[Finding] = field(default_factory=list)

    # Stats — useful for the markdown report header
    stats: dict = field(default_factory=dict)

    def findings_by_severity(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {s.value: [] for s in Severity}
        for f in self.findings:
            out[f.severity.value].append(f)
        return out

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "languages": self.languages,
            "file_count": self.file_count,
            "dependency_graph": self.dependency_graph,
            "node_meta": self.node_meta,
            "reachability": self.reachability,
            "tool_graph": self.tool_graph,
            "findings": [f.to_dict() for f in self.findings],
            "stats": self.stats,
        }


# Default exclusions — anything in these directory names is skipped at any depth.
# Consumers can override via codebase_map(..., extra_excludes=...).
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    # Python venvs (any name pattern)
    "venv", ".venv", "venv-music", "venv-tts", "env", "site-packages",
    # Build / cache / VCS
    "__pycache__", "node_modules", ".git", ".next",
    "dist", "build", "htmlcov", ".pytest_cache", "coverage",
    ".cursor", ".vscode", ".ipynb_checkpoints",
    # Vendored ML / heavy libs (Guaardvark-specific)
    "ComfyUI", "voice",
    # Stale swarm worktrees
    ".swarm-worktrees",
    # Non-source data dirs (Guaardvark-specific but generally safe)
    "data", "logs", "backups", "pids", "plans", "audit", "outputs",
    # Migrations rarely have testable logic
    "migrations",
})


def is_excluded(path: Path, extra_excludes: frozenset[str] = frozenset()) -> bool:
    excludes = DEFAULT_EXCLUDE_DIRS | extra_excludes
    return any(part in excludes for part in path.parts)


def filter_findings(
    findings: list[dict],
    *,
    severities: set[str] | None = None,
    kinds: set[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Filter a list of finding dicts by severity / kind, then optionally cap.

    Operates on the dict form (as returned by Finding.to_dict() / cached in the
    snapshot). `severities` and `kinds`, when given, are inclusive allow-sets;
    `None` means "no filter on that axis". `limit` caps the result length.
    Order is preserved (callers rank before filtering)."""
    out = findings
    if severities:
        out = [f for f in out if f.get("severity") in severities]
    if kinds:
        out = [f for f in out if f.get("kind") in kinds]
    if limit is not None and limit >= 0:
        out = out[:limit]
    return list(out)


def _path_to_module(rel_str: str) -> str:
    """Convert a rel path like 'backend/services/foo.py' to 'backend.services.foo'.

    Mirrors dependency_graph._module_name so dormant post-filtering can match the
    dispatch_graph dynamic-module set by dotted name."""
    rel = Path(rel_str)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def codebase_map(
    root_path: str | Path,
    extra_excludes: frozenset[str] = frozenset(),
) -> SystemMap:
    """Generate a SystemMap for the codebase at root_path.

    Imports each analyzer locally so a failure in one doesn't break the others —
    self-improvement consumers want partial results when one analyzer chokes.
    """
    from . import dependency_graph, reachability, tool_graph, dead_symbol, dispatch_graph

    root = Path(root_path).resolve()
    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")

    smap = SystemMap(
        root=str(root),
        generated_at=time.time(),
        languages=["python", "javascript"],
        file_count=0,
        stats={},
    )

    # 0. Dynamic-dispatch graph (B3): which modules are reached only by string-keyed
    #    dispatch (Celery task names, blueprint auto-reg, plugin.json, tool tables).
    #    Used to suppress false-positive dormant findings. Never raises.
    dynamic_modules: set[str] = set()
    try:
        dispatch_result = dispatch_graph.analyze(root, extra_excludes)
        dynamic_modules = set(dispatch_result["graph"].get("dynamic_modules", []))
        smap.stats["dispatch"] = dispatch_result["stats"]
    except Exception as e:
        smap.stats["dispatch"] = {"error": str(e)}

    # 1. Dependency graph (cheapest, must run first — others may use its file list)
    try:
        dep_result = dependency_graph.analyze(root, extra_excludes,
                                              dynamic_modules=dynamic_modules)
        smap.dependency_graph = dep_result["graph"]
        smap.node_meta = dep_result.get("node_meta", {})
        smap.findings.extend(dep_result["findings"])
        smap.file_count = dep_result["file_count"]
        smap.stats["dependency"] = dep_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.IMPORT_CYCLE,
            severity=Severity.INFO,
            summary=f"dependency_graph analyzer failed: {e}",
        ))

    # 1b. Belt-and-suspenders: even if a dependency_graph version didn't accept the
    #     dynamic set, post-filter dormant findings for dynamically-reached modules.
    if dynamic_modules:
        kept: list[Finding] = []
        for f in smap.findings:
            if f.kind == FindingKind.DORMANT_MODULE:
                mod = _path_to_module(f.paths[0]) if f.paths else None
                if mod and mod in dynamic_modules:
                    continue
            kept.append(f)
        smap.findings = kept

    # 2. Reachability (frontend ↔ backend)
    try:
        reach_result = reachability.analyze(root, extra_excludes)
        smap.reachability = reach_result["graph"]
        smap.findings.extend(reach_result["findings"])
        smap.stats["reachability"] = reach_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.GHOST_ENDPOINT,
            severity=Severity.INFO,
            summary=f"reachability analyzer failed: {e}",
        ))

    # 3. Tool graph (Guaardvark-specific; gracefully no-ops elsewhere)
    try:
        tool_result = tool_graph.analyze(root, extra_excludes)
        smap.tool_graph = tool_result["graph"]
        smap.findings.extend(tool_result["findings"])
        smap.stats["tool"] = tool_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.UNWIRED_TOOL,
            severity=Severity.INFO,
            summary=f"tool_graph analyzer failed: {e}",
        ))

    # 4. Dead-symbol (function-level static dead-code; advisory only, NEVER
    #    auto-dispatched — see DISPATCHABLE_KINDS, deliberately excludes this kind).
    try:
        dead_result = dead_symbol.analyze(root, extra_excludes)
        smap.findings.extend(dead_result["findings"])
        smap.stats["dead_symbol"] = dead_result["stats"]
    except Exception as e:
        smap.findings.append(Finding(
            kind=FindingKind.DEAD_SYMBOL,
            severity=Severity.INFO,
            summary=f"dead_symbol analyzer failed: {e}",
        ))

    return smap
