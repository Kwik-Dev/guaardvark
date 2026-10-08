"""The screen agent's knowledge files say true things about this install.

The lesson reconciler (lesson_reconciler.py) and the session expectations in
agent_control_service.py point at these files by line number, so the
hand-written part above the auto-distilled marker keeps its line count.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

AGENT_DIR = Path(__file__).resolve().parents[2] / "data" / "agent"
FILES = ("self_knowledge.md", "self_knowledge_compact.md")
MARKER = "<!-- AUTO-DISTILLED STRATEGIES -->"
# Line of the marker in each file. Moving it moves every line a saved belief row points at.
MARKER_LINE = {"self_knowledge.md": 42, "self_knowledge_compact.md": 40}


@pytest.mark.parametrize("name", FILES)
def test_no_fixed_port_or_version(name):
    text = (AGENT_DIR / name).read_text(encoding="utf-8")
    assert "5175" not in text
    assert "v2.6.2" not in text
    assert "{VITE_PORT}" in text


@pytest.mark.parametrize("name", FILES)
def test_hand_written_part_keeps_its_line_numbers(name):
    lines = (AGENT_DIR / name).read_text(encoding="utf-8").splitlines()
    assert lines.index(MARKER) + 1 == MARKER_LINE[name]


def test_ctrl_l_advice_agrees_with_the_navigate_action():
    # navigate presses Ctrl+L itself; the file also says not to press it by hand.
    text = (AGENT_DIR / "self_knowledge.md").read_text(encoding="utf-8")
    assert "Direct navigation (Ctrl+L)" not in text
    assert "| `Ctrl+L` | **Critical** |" not in text


@pytest.fixture
def knowledge_root(tmp_path, monkeypatch):
    root = tmp_path
    (root / "data" / "agent").mkdir(parents=True)
    monkeypatch.setattr("backend.config.GUAARDVARK_ROOT", str(root))
    monkeypatch.setenv("VITE_PORT", "5199")
    return root / "data" / "agent"


def test_loaders_fill_in_this_installs_port(knowledge_root):
    from backend.services.agent_control_service import AgentControlService

    (knowledge_root / "self_knowledge_compact.md").write_text("- Chat: `localhost:{VITE_PORT}/chat`\n")
    (knowledge_root / "self_knowledge.md").write_text(
        'UI at `localhost:{VITE_PORT}`. Example: {"action": "navigate", "url": "{1}"}\n')

    compact = AgentControlService._load_self_knowledge_compact()
    long = AgentControlService._load_self_knowledge()

    assert compact == "- Chat: `localhost:5199/chat`"
    assert "localhost:5199" in long
    assert '{"action": "navigate", "url": "{1}"}' in long
    assert "{VITE_PORT}" not in compact + long


def test_the_real_files_load_with_no_placeholder_left(monkeypatch):
    from backend.services.agent_control_service import AgentControlService

    monkeypatch.setattr("backend.config.GUAARDVARK_ROOT", str(AGENT_DIR.parents[1]))
    monkeypatch.delenv("VITE_PORT", raising=False)
    compact = AgentControlService._load_self_knowledge_compact()
    assert "localhost:5173/chat" in compact
    assert "{VITE_PORT}" not in compact + AgentControlService._load_self_knowledge()
