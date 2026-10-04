"""The shared fixtures must work.

`fake_backend` replaces the CLI's transport by patching `LlxClient.__init__`. That
is the load-bearing trick of the whole test framework: if it silently stopped
working, every future contract test would either hit a real backend or pass against
nothing. Until this file, no test used the fixture, so a broken one would have gone
unnoticed.
"""
from __future__ import annotations

import json


def test_fake_backend_intercepts_the_cli(fake_backend, cli_runner, isolated_home):
    """A real command, through the real CLI, against the fake backend."""
    from llx.main import app

    fake_backend.route("GET", "/api/clients", json=[
        {"id": 1, "name": "Acme", "project_count": 2},
        {"id": 2, "name": "Globex"},
    ])

    result = cli_runner.invoke(app, ["clients", "list", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert [c["id"] for c in payload["data"]["clients"]] == [1, 2]

    # The request really went through the patched transport, not the network.
    assert len(fake_backend.calls_for("GET", "/api/clients")) == 1
    assert fake_backend.calls[0][0] == "GET"


def test_an_undeclared_route_fails_loudly(fake_backend, cli_runner, isolated_home):
    """An unmocked route must not look like success."""
    from llx.main import app

    result = cli_runner.invoke(app, ["clients", "list", "--json"])

    assert result.exit_code != 0
    assert "no route" in (result.output or "").lower()


def test_isolated_home_keeps_the_developer_config_out_of_it(isolated_home):
    """The CLI must read and write a throwaway config, not the real one.

    `CONFIG_FILE` is resolved from `Path.home()` at import time, so this asserts the
    fixture re-points the resolved path — merely setting HOME would be a no-op for an
    already-imported module, and a test that cannot fail is worse than no test.
    """
    from llx import config

    assert str(config.CONFIG_FILE).startswith(str(isolated_home)), config.CONFIG_FILE
    assert str(config.CONFIG_FILE).endswith("cli.json")
