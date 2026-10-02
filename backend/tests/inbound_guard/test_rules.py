"""Each inbound rule fires on what it describes and stays quiet on the near misses.

The trigger text lives in cases.json, so this module carries none of it and the
guard does not flag its own tests.
"""
import json
from pathlib import Path

import pytest

from scripts.inbound_guard import change_from_texts, scan
from scripts.inbound_guard.rules import RuleSet, visible

CASES = json.loads((Path(__file__).with_name("cases.json")).read_text())["cases"]


def _verdict(case):
    change = change_from_texts(case["path"], case["old"] or None, case["new"])
    return scan([change], source="cli", subject=case["name"], mode="observe")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    rules = {f.rule for f in _verdict(case).findings}
    assert set(case["expect"]) <= rules, f"missing {set(case['expect']) - rules}"
    assert not (set(case.get("expect_not", [])) & rules)


def _case(name):
    return next(c for c in CASES if c["name"] == name)


def test_reordering_characters_are_refused_and_shell_is_held():
    assert _verdict(_case("bidi control")).verdict == "block"
    assert _verdict(_case("shell through subprocess")).verdict == "hold"
    assert _verdict(_case("torch.load with weights_only")).verdict == "allow"


def test_excerpts_show_control_characters_as_escapes():
    finding = next(f for f in _verdict(_case("bidi control")).findings if f.rule == "trojan.bidi")
    assert "\\u202e" in finding.excerpt
    assert "\u202e" not in finding.excerpt
    assert visible("a\u200bb") == "a\\u200bb"


def test_enforce_flag_follows_mode():
    case = _case("shell through subprocess")
    change = change_from_texts(case["path"], None, case["new"])
    assert scan([change], source="cli", subject="x", mode="enforce").enforced
    assert not scan([change], source="cli", subject="x", mode="observe").enforced


def test_findings_per_rule_per_file_are_capped():
    new = "".join("os." + "system(cmd)\n" for _ in range(12))
    verdict = scan([change_from_texts("backend/x.py", None, new)], source="cli", subject="x", mode="observe")
    hits = [f for f in verdict.findings if f.rule == "exec.os-system"]
    assert len(hits) == 6  # five examples and one "and 7 more"
    assert any("and 7 more" in f.why for f in hits)


def test_rule_data_is_well_formed():
    rules = RuleSet.load()
    ids = [r["id"] for r in rules.data["line_rules"] + rules.data["path_rules"]]
    assert len(ids) == len(set(ids))
    for rule in rules.data["line_rules"] + rules.data["path_rules"]:
        assert rule["severity"] in ("info", "low", "medium", "high", "critical")
        assert rule["why"]
        for name in rule["paths"]:
            if name.startswith("@"):
                assert name[1:] in rules.groups, name


def test_extension_scanner_adds_findings_and_a_failing_one_is_recorded():
    from scripts.inbound_guard import Finding

    change = change_from_texts("backend/x.py", None, "x = 1\n")

    def adds(changes, ctx):
        return [Finding("ext.sample", "extension", "high", "backend/x.py", 1, "x = 1", "an extension's finding")]

    def breaks(changes, ctx):
        raise RuntimeError("down")

    verdict = scan([change], source="cli", subject="x", mode="observe",
                   scanners=[("adds", adds), ("breaks", breaks)])
    assert verdict.verdict == "hold"
    assert verdict.providers == ["adds"]
    assert any("breaks" in e for e in verdict.errors)
