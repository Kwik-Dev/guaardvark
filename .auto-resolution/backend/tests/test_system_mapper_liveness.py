"""System-mapper Phase 2/3 tests: B3 (dispatch graph) + B4 (liveness consensus).

Pure unit tests over synthetic fixtures — no DB, no live services. Phase 1 has
not collected real hits yet, so SymbolHit rows here are constructed by hand.
"""

import datetime as _dt
from pathlib import Path

import pytest

try:
    from backend.services.system_mapper import dispatch_graph, dependency_graph, liveness
    from backend.services.system_mapper.core import FindingKind, Severity
    from backend.services.system_mapper.actions import DISPATCHABLE_KINDS
except Exception:
    pytest.skip("system_mapper not importable", allow_module_level=True)


# ============================================================================
# B3: dispatch_graph — a module reached only via a Celery task-name string is
#     NOT flagged dormant after dispatch_graph runs; an import-dead module is.
# ============================================================================

def _write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_celery_task_module_not_flagged_dormant(tmp_path):
    root = tmp_path

    # A Celery task module: nothing imports it, but it's registered by task name.
    _write(root / "backend" / "tasks" / "lonely_task.py", (
        "from celery import shared_task\n"
        "@shared_task(name='lonely.do_thing')\n"
        "def do_thing():\n"
        "    return 1\n"
    ))
    # A genuinely import-dead module: nothing imports it, no dispatch reaches it.
    _write(root / "backend" / "services" / "import_dead.py", (
        "def orphan():\n"
        "    return 2\n"
    ))
    # A harmless importer so the graph isn't trivially empty.
    _write(root / "backend" / "services" / "anchor.py", "x = 1\n")

    # dispatch_graph sees the task module as dynamically reached.
    dres = dispatch_graph.analyze(root)
    dynamic = set(dres["graph"]["dynamic_modules"])
    assert "backend.tasks.lonely_task" in dynamic
    assert "backend.services.import_dead" not in dynamic

    # dependency_graph WITH the dynamic set: task module suppressed, dead flagged.
    dep = dependency_graph.analyze(root, dynamic_modules=dynamic)
    dormant_paths = {
        f.paths[0] for f in dep["findings"]
        if f.kind == FindingKind.DORMANT_MODULE and f.paths
    }
    assert "backend/services/import_dead.py" in dormant_paths
    assert "backend/tasks/lonely_task.py" not in dormant_paths
    assert dep["stats"]["dormant_suppressed_dynamic"] >= 1


def test_dependency_graph_without_dynamic_set_flags_task_module(tmp_path):
    """Sanity: without the dynamic set, the task module WOULD be flagged dormant
    (this is the false positive B3 fixes)."""
    root = tmp_path
    _write(root / "backend" / "tasks" / "lonely_task.py", (
        "from celery import shared_task\n"
        "@shared_task(name='lonely.do_thing')\n"
        "def do_thing():\n"
        "    return 1\n"
    ))
    _write(root / "backend" / "services" / "anchor.py", "x = 1\n")

    dep = dependency_graph.analyze(root)  # no dynamic set
    dormant_paths = {
        f.paths[0] for f in dep["findings"]
        if f.kind == FindingKind.DORMANT_MODULE and f.paths
    }
    assert "backend/tasks/lonely_task.py" in dormant_paths


def test_dispatch_graph_blueprint_and_tool_detection(tmp_path):
    root = tmp_path
    _write(root / "backend" / "api" / "things_api.py", (
        "from flask import Blueprint\n"
        "bp = Blueprint('things', __name__)\n"
    ))
    _write(root / "backend" / "tools" / "my_tool.py", (
        "class MyTool:\n"
        "    name = 'my_tool'\n"
        "    def execute(self):\n"
        "        return 1\n"
    ))
    dres = dispatch_graph.analyze(root)
    dynamic = set(dres["graph"]["dynamic_modules"])
    assert "backend.api.things_api" in dynamic
    assert "backend.tools.my_tool" in dynamic
    assert dres["findings"] == []  # B3 never emits findings


def test_dispatch_graph_never_raises_on_bad_tree(tmp_path):
    root = tmp_path
    _write(root / "backend" / "tasks" / "broken.py", "def (((((\n")  # unparseable
    res = dispatch_graph.analyze(root)
    assert isinstance(res["graph"]["dynamic_modules"], list)
    assert res["findings"] == []


# ============================================================================
# B4: liveness consensus.
# ============================================================================

def _hit(symbol_id, module, display_name, last_fired_at, static_reach=None,
         hit_count=1, kind="task"):
    return {
        "symbol_id": symbol_id,
        "symbol_kind": kind,
        "display_name": display_name,
        "module": module,
        "last_fired_at": last_fired_at,
        "static_reachability": static_reach,
        "hit_count": hit_count,
    }


def _fake_map():
    # live_mod is active in the static map; ghost_mod is absent. tool 'cool_tool'
    # is registered (registry-live).
    return {
        "node_meta": {
            "backend.services.live_mod": {"lifecycle": "active", "importers": 3},
        },
        "tool_graph": {
            "registered_tools": [{"name": "cool_tool", "wired": True}],
            "core_tools": ["cool_tool"],
        },
        "reachability": {"routes": []},
        "findings": [], "stats": {},
    }


def test_runtime_zombie_for_reachable_not_fired():
    now = _dt.datetime(2026, 5, 31, 12, 0, 0)
    old = now - _dt.timedelta(days=120)
    hits = [
        _hit("task:zombie", "backend.services.live_mod", "zombie", old,
             static_reach=True),
    ]
    res = liveness.analyze(hits, _fake_map(), window_days=30, now=now)
    kinds = [f.kind for f in res["findings"]]
    assert FindingKind.RUNTIME_ZOMBIE in kinds
    z = next(f for f in res["findings"] if f.kind == FindingKind.RUNTIME_ZOMBIE)
    # evidence carries the consensus detail
    assert z.evidence["runtime"] == 0.0
    assert z.evidence["static"] == 1.0
    assert z.evidence["window_days"] == 30
    assert "confidence" in z.evidence


def test_contextual_discovery_for_fired_not_imported():
    now = _dt.datetime(2026, 5, 31, 12, 0, 0)
    hits = [
        _hit("task:discovered", "backend.tasks.ghost_mod", "discovered", now,
             static_reach=None),
    ]
    res = liveness.analyze(hits, _fake_map(), window_days=30, now=now)
    kinds = [f.kind for f in res["findings"]]
    assert FindingKind.CONTEXTUAL_DISCOVERY in kinds
    d = next(f for f in res["findings"] if f.kind == FindingKind.CONTEXTUAL_DISCOVERY)
    assert d.severity == Severity.INFO
    assert d.evidence["runtime"] == 1.0
    assert d.evidence["static"] == 0.0


def test_liveness_kinds_never_dispatchable():
    """CRITICAL guard: none of the three liveness kinds are auto-dispatchable.
    A tracing window that misses a rare handler must never auto-delete it."""
    for kind in (FindingKind.RUNTIME_ZOMBIE,
                 FindingKind.HOT_PATH_SPIKE,
                 FindingKind.CONTEXTUAL_DISCOVERY):
        assert kind.value not in DISPATCHABLE_KINDS, (
            f"{kind.value} must NOT be dispatchable"
        )
    # Also confirm dead-symbol stays out.
    assert FindingKind.DEAD_SYMBOL.value not in DISPATCHABLE_KINDS


def test_confidence_math_at_boundaries():
    now = _dt.datetime(2026, 5, 31, 12, 0, 0)
    fmap = _fake_map()
    reg = liveness._registry_names(fmap)
    stat = liveness._static_modules(fmap)

    # all three signals present: 0.6 + 0.3 + 0.1 = 1.0
    full = liveness.compute_consensus(
        _hit("tool:cool_tool", "backend.services.live_mod", "cool_tool", now,
             static_reach=True),
        reg, stat, now, 30,
    )
    assert full["runtime"] == 1.0 and full["registry"] == 1.0 and full["static"] == 1.0
    assert full["confidence"] == pytest.approx(1.0)

    # none present: 0.0
    none_ = liveness.compute_consensus(
        _hit("task:nothing", "backend.unknown.mod", "nothing",
             now - _dt.timedelta(days=999), static_reach=False),
        reg, stat, now, 30,
    )
    assert none_["confidence"] == pytest.approx(0.0)

    # runtime only: 0.6
    rt = liveness.compute_consensus(
        _hit("task:rt", "backend.unknown.mod", "rt", now, static_reach=False),
        reg, stat, now, 30,
    )
    assert rt["confidence"] == pytest.approx(0.6)

    # static only: 0.1
    st = liveness.compute_consensus(
        _hit("task:st", "backend.services.live_mod", "st",
             now - _dt.timedelta(days=999), static_reach=True),
        reg, stat, now, 30,
    )
    assert st["confidence"] == pytest.approx(0.1)

    # registry only: 0.3
    rg = liveness.compute_consensus(
        _hit("tool:cool_tool", "backend.unknown.mod", "cool_tool",
             now - _dt.timedelta(days=999), static_reach=False),
        reg, stat, now, 30,
    )
    assert rg["confidence"] == pytest.approx(0.3)


def test_window_boundary_exact_edge_counts_as_live():
    now = _dt.datetime(2026, 5, 31, 12, 0, 0)
    edge = now - _dt.timedelta(days=30)  # exactly at the window edge
    reg, stat = set(), set()
    c = liveness.compute_consensus(
        _hit("task:edge", "m", "edge", edge, static_reach=False),
        reg, stat, now, 30,
    )
    assert c["runtime"] == 1.0  # >= window edge counts as fired-within


def test_liveness_never_raises_on_garbage():
    res = liveness.analyze([{"bogus": 1}], {"not": "a map"}, window_days=30)
    assert "findings" in res and "stats" in res
