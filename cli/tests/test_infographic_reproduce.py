"""`infographic jobs` / `infographic reproduce` read the recorded spec (issue #8)."""
from __future__ import annotations

import json

from llx.main import app

_ROW = {
    "id": 3, "status": "completed",
    "inputs": {"scene": "five facts about bees", "raw_prompt": "", "title": "Bees",
               "footer": "", "style": "editorial", "aspect": "16:9",
               "hashtags": ["bees"], "callouts": []},
    "seed": 42, "width": 1216, "height": 684, "filename": "info_3.png",
    "image_url": "/api/infographic/view?filename=info_3.png",
}


def test_infographic_jobs_lists_records(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/infographic/generations",
                       json={"success": True, "generations": [_ROW]})

    result = cli_runner.invoke(app, ["infographic", "jobs", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["generations"][0]["id"] == 3


def test_infographic_reproduce_emits_the_full_spec_including_seed(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/infographic/generations/3",
                       json={"success": True, "generation": _ROW})

    result = cli_runner.invoke(app, ["infographic", "reproduce", "3", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sent"] is False
    assert payload["path"] == "/api/infographic/generate"
    assert payload["body"]["scene"] == "five facts about bees"
    assert payload["body"]["seed"] == 42
    assert payload["inexpressible"] == []
    assert payload["named_command_line"].startswith("guaardvark infographic generate")
    assert "--seed 42" in payload["named_command_line"]
    assert fake_backend.posted_paths() == []


def test_infographic_reproduce_yes_reruns_and_is_audited(fake_backend, cli_runner, isolated_home):
    from llx.commands._fork import _api_guard as guard

    fake_backend.route("GET", "/api/infographic/generations/3",
                       json={"success": True, "generation": _ROW})
    fake_backend.route("POST", "/api/infographic/generate", json={"success": True, "generation_id": 4})

    result = cli_runner.invoke(app, ["infographic", "reproduce", "3", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    calls = fake_backend.calls_for("POST", "/api/infographic/generate")
    assert calls, fake_backend.calls
    body = json.loads(calls[0][2])
    assert body["scene"] == "five facts about bees" and body["seed"] == 42
    entries = guard.read_audit(limit=5)
    assert any(e.get("outcome") == "ok"
               and "/api/infographic/generate" in str(e.get("path")) for e in entries), entries


def test_infographic_reproduce_without_a_spec_exits_2(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/infographic/generations/9",
                       json={"success": True, "generation": {"id": 9, "inputs": {}, "seed": None}})

    result = cli_runner.invoke(app, ["infographic", "reproduce", "9", "--json"])

    assert result.exit_code == 2, result.output
    assert fake_backend.posted_paths() == []
