"""Recipe counters live in a sidecar; a disabled recipe is neither matched nor advertised."""
import json
import os
import sys

import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from backend.services import recipe_stats


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data" / "agent").mkdir(parents=True)
    recipes = tmp_path / "data" / "agent" / "recipes.json"
    recipes.write_text(json.dumps({
        "_meta": {"description": "test"},
        "open_thing": {"description": "Open the thing", "triggers": ["^open the thing$"],
                       "steps": [{"action": "hotkey", "keys": ["ctrl", "t"]}]},
        "other": {"description": "Other", "triggers": ["^other$"],
                  "steps": [{"action": "hotkey", "keys": ["ctrl", "t"]}]},
    }, indent=2))
    with patch("backend.config.GUAARDVARK_ROOT", str(tmp_path)):
        yield tmp_path


def test_sidecar_writes_leave_recipes_json_untouched(root):
    before = (root / "data" / "agent" / "recipes.json").read_bytes()
    recipe_stats.touch_run("open_thing")
    recipe_stats.bump("open_thing", ups=1)
    recipe_stats.set_disabled("open_thing", True, reason="thumbs_down:1")
    assert (root / "data" / "agent" / "recipes.json").read_bytes() == before
    data = json.loads((root / "data" / "agent" / "recipe_stats.json").read_text())
    assert data["open_thing"]["runs"] == 1 and data["open_thing"]["ups"] == 1
    assert data["open_thing"]["disabled"] is True and data["open_thing"]["disabled_reason"] == "thumbs_down:1"
    assert data["open_thing"]["last_used"]
    recipe_stats.bump("open_thing", ups=-5)
    assert recipe_stats.get("open_thing")["ups"] == 0, "counters never go negative"


def test_disabled_recipe_is_skipped_by_the_matcher_and_left_out_of_the_index(root):
    from backend.services.agent_control_service import AgentControlService
    AgentControlService._recipe_cache = None
    AgentControlService._recipe_mtime = 0.0
    recipe_stats.set_disabled("open_thing", True, reason="thumbs_down:7")
    recipe_stats.set_provisional("other", True)
    svc = AgentControlService.__new__(AgentControlService)
    svc._execute_recipe = MagicMock(return_value="ran")
    svc._preconditions_pass = lambda recipe, screen: True
    svc._is_firefox_running = lambda screen: False
    assert svc._try_recipe("open the thing", MagicMock()) is None, "disabled: not matched"
    assert svc._try_recipe("other", MagicMock()) == "ran"
    svc._execute_recipe.assert_called_once()
    index = AgentControlService._load_recipe_index()
    assert "open_thing" not in index
    assert "- other: Other (provisional)" in index
    recipe_stats.set_disabled("open_thing", False)
    assert svc._try_recipe("open the thing", MagicMock()) == "ran"
