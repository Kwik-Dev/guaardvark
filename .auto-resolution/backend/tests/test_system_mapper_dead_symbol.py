"""Tests for the function-level dead-symbol analyzer.

Pure-logic — builds a tiny fake repo on disk, no DB/app fixtures. Verifies:
  - a function nobody calls is flagged DEAD_SYMBOL
  - a function called from another module is NOT flagged
  - a function referenced only by a name-string (getattr/task name) is NOT flagged
  - a decorated function (@app.route) is NOT flagged
  - DEAD_SYMBOL is never in DISPATCHABLE_KINDS
"""
from backend.services.system_mapper import dead_symbol, actions
from backend.services.system_mapper.core import FindingKind, Severity


def _write(p, text=""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _dead_names(result):
    return {
        f.evidence.get("symbol")
        for f in result["findings"]
        if f.kind == FindingKind.DEAD_SYMBOL and f.evidence.get("symbol")
    }


def _fake_repo(tmp_path):
    # orphan_func: defined, referenced nowhere → DEAD
    # used_func:   defined here, called from caller.py → LIVE
    # dispatched_func: only referenced via a string literal → LIVE (conservative)
    # route_handler: decorated with @app.route → LIVE (conservative)
    _write(
        tmp_path / "backend" / "services" / "thing.py",
        "def orphan_func():\n"
        "    return 1\n"
        "\n"
        "def used_func():\n"
        "    return 2\n"
        "\n"
        "def dispatched_func():\n"
        "    return 3\n",
    )
    _write(
        tmp_path / "backend" / "services" / "caller.py",
        "from backend.services.thing import used_func\n"
        "\n"
        "def go():\n"
        "    return used_func()\n"
        "\n"
        "DISPATCH = {'do_it': 'dispatched_func'}\n",
    )
    _write(
        tmp_path / "backend" / "api" / "web.py",
        "app = object()\n"
        "@app.route('/x')\n"
        "def route_handler():\n"
        "    return 'ok'\n",
    )
    return tmp_path


def test_uncalled_function_is_flagged(tmp_path):
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    assert "orphan_func" in _dead_names(result)


def test_cross_module_call_not_flagged(tmp_path):
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    dead = _dead_names(result)
    assert "used_func" not in dead
    # go() itself is referenced nowhere — but it has no string/decorator cover,
    # so it legitimately IS a dead candidate. We only assert used_func is live.


def test_string_referenced_function_not_flagged(tmp_path):
    """A function whose name only appears as a string literal (simulating
    getattr / Celery task name / Flask endpoint dispatch) must NOT be flagged."""
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    assert "dispatched_func" not in _dead_names(result)


def test_decorated_function_not_flagged(tmp_path):
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    assert "route_handler" not in _dead_names(result)


def test_dunder_and_test_functions_not_flagged(tmp_path):
    _write(
        tmp_path / "backend" / "services" / "mod.py",
        "class C:\n"
        "    def __init__(self):\n"
        "        pass\n"
        "\n"
        "def test_something():\n"
        "    pass\n",
    )
    result = dead_symbol.analyze(tmp_path)
    dead = _dead_names(result)
    assert "__init__" not in dead
    assert "test_something" not in dead


def test_framework_method_name_not_flagged(tmp_path):
    """A class method named like a framework override (e.g. execute) is treated
    as live even with no static reference."""
    _write(
        tmp_path / "backend" / "tools" / "mytool.py",
        "class MyTool:\n"
        "    def execute(self, x):\n"
        "        return x\n"
        "\n"
        "    def private_helper_xyz(self):\n"
        "        return 1\n",
    )
    result = dead_symbol.analyze(tmp_path)
    dead = _dead_names(result)
    assert "execute" not in dead
    # a non-framework, unreferenced method IS a candidate
    assert "private_helper_xyz" in dead


def test_returns_expected_shape_and_never_raises(tmp_path):
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    assert set(result) == {"graph", "findings", "stats"}
    assert isinstance(result["findings"], list)
    assert "dead_symbols" in result["stats"]


def test_severity_is_advisory(tmp_path):
    result = dead_symbol.analyze(_fake_repo(tmp_path))
    dead = [f for f in result["findings"] if f.kind == FindingKind.DEAD_SYMBOL]
    assert dead
    assert all(f.severity in (Severity.INFO, Severity.LOW) for f in dead)


def test_dead_symbol_not_dispatchable():
    """Critical safety invariant: static dead-symbol detection must NEVER
    auto-dispatch to the fix engine."""
    assert FindingKind.DEAD_SYMBOL.value not in actions.DISPATCHABLE_KINDS


def test_registered_in_codebase_map(tmp_path):
    """The analyzer is wired into codebase_map() and contributes findings."""
    from backend.services.system_mapper import codebase_map
    smap = codebase_map(_fake_repo(tmp_path))
    assert "dead_symbol" in smap.stats
    dead = [f for f in smap.findings if f.kind == FindingKind.DEAD_SYMBOL]
    assert dead
