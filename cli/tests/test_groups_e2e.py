"""One read-only smoke per command group, against the real backend.

The fixture suites ask "did the CLI send the request it claims to?". This tier asks a
different question at the same seam: "does a real backend answer that request the way
the `--json` envelope promises?". It is a seam test, not a backend test suite, so it
spends one call per group and stops there.

Read-only by construction. Nothing here creates, renders, trains or downloads, and
`inprocess_backend` refuses to boot without an explicit scratch Postgres database, so
the tier cannot land on the live dev database by accident. Every case also asserts the
backend actually served the route it expected -- the transport is address-agnostic, so
without that a command that answered entirely in-process would still pass. Groups that
would need a plugin service running are skipped with a reason rather than quietly
dropped -- a silent skip reads as a passing feature (backend/tests/conftest.py
`_SILENT_GUARD_REASONS`).
"""
from __future__ import annotations

import json

import pytest

from llx.main import app

pytestmark = pytest.mark.e2e


def _invoke(cli_runner, argv):
    return cli_runner.invoke(app, [*argv, "--json"])


def _envelope(cli_runner, backend, argv, route):
    """Run a command, assert the JSON envelope and that `route` was really served."""
    result = _invoke(cli_runner, argv)
    assert result.exit_code == 0, result.output
    method, path = route
    assert backend.called(method, path), (
        f"{' '.join(argv)}: expected {method} {path}, "
        f"backend served {backend.calls}"
    )
    payload = json.loads(result.output)
    assert payload["status"] == "success", payload
    assert isinstance(payload["data"], dict), payload
    return payload["data"]


# (id, argv, the top-level keys the group's --json contract promises, the route it hits)
_ENVELOPE_CASES = [
    ("guard status", ["guard", "status"], ("mode", "git"), ("GET", "/api/settings/inbound_guard")),
    ("improve status", ["improve", "status"], ("enabled",), ("GET", "/api/self-improvement/status")),
    ("system-map findings", ["system-map", "findings"], ("findings",), ("GET", "/api/system-map/findings")),
    ("content pages", ["content", "pages"], ("pages",), ("GET", "/api/content/pages")),
    ("connections list", ["connections", "list"], ("connections",), ("GET", "/api/connections")),
    ("approvals list", ["approvals", "list"], ("drafts", "held"), ("GET", "/api/connections/publishes")),
    ("cast list", ["cast", "list"], ("subjects",), ("GET", "/api/cast-library")),
    ("llm provider", ["llm", "provider"], ("provider", "providers"), ("GET", "/api/llm/provider")),
    ("llm models", ["llm", "models"], ("models", "provider"), ("GET", "/api/llm/provider/models")),
    ("settings list", ["settings", "list"], ("settings", "settable"), ("GET", "/api/settings")),
    ("training datasets", ["training", "datasets"], ("datasets",), ("GET", "/api/training_datasets")),
    ("training backends", ["training", "backends"], ("backends",), ("GET", "/api/plugins")),
    ("wordpress sites", ["wordpress", "sites"], ("sites",), ("GET", "/api/wordpress/sites")),
    ("agents list", ["agents", "list"], ("agents",), ("GET", "/api/agents")),
    ("jobs list", ["jobs", "list"], ("jobs",), ("GET", "/api/meta/active_jobs")),
    ("projects list", ["projects", "list"], ("projects",), ("GET", "/api/projects")),
    ("files list", ["files", "list"], ("folders", "documents"), ("GET", "/api/files/browse")),
    ("family status", ["family", "status"], ("status",), ("GET", "/api/interconnector/status")),
    ("websearch status", ["websearch", "status"], ("service_status", "services"), ("GET", "/api/web-search/status")),
    # These two read plugin/extension *state* rather than driving a plugin service,
    # so they answer in-process without anything running. Verified against the real
    # routes; they are not skipped. (`video-editor health` is not one of them -- see
    # the skip at the bottom of this file.)
    ("infographic models", ["infographic", "models"], ("models",), ("GET", "/api/infographic/models")),
    ("video-editor projects", ["video-editor", "projects"], ("projects",), ("GET", "/api/video-editor/projects")),
    ("plugins list", ["plugins", "list"], ("plugins",), ("GET", "/api/plugins")),
]


@pytest.mark.parametrize(
    "argv,keys,route",
    [(case[1], case[2], case[3]) for case in _ENVELOPE_CASES],
    ids=[case[0] for case in _ENVELOPE_CASES],
)
def test_group_replies_with_the_documented_envelope(
    inprocess_backend, cli_runner, isolated_home, argv, keys, route
):
    data = _envelope(cli_runner, inprocess_backend, argv, route)

    missing = [key for key in keys if key not in data]
    assert not missing, f"{' '.join(argv)}: missing {missing} from {sorted(data)}"


def test_approvals_list_reaches_every_source(inprocess_backend, cli_runner, isolated_home):
    """`approvals list` merges three sources and reports a dead one in `errors` while
    still exiting 0, so a presence-only check cannot tell a live source from a failed
    one -- assert the merge was clean."""
    data = _envelope(
        cli_runner, inprocess_backend, ["approvals", "list"], ("GET", "/api/connections/publishes")
    )

    assert data["errors"] == {}, data["errors"]
    for source in ("drafts", "held"):
        assert source in data


# Groups whose --json output is a bare array, not the `{status, data}` envelope.
_LIST_CASES = [
    ("film-crew list", ["film-crew", "list"], ("GET", "/api/production")),
    ("music-video list", ["music-video", "list"], ("GET", "/api/music-video")),
]


@pytest.mark.parametrize(
    "argv,route", [(case[1], case[2]) for case in _LIST_CASES],
    ids=[case[0] for case in _LIST_CASES],
)
def test_list_group_replies_with_a_json_array(inprocess_backend, cli_runner, isolated_home, argv, route):
    result = _invoke(cli_runner, argv)

    assert result.exit_code == 0, result.output
    method, path = route
    assert inprocess_backend.called(method, path), (
        f"{' '.join(argv)}: expected {method} {path}, backend served {inprocess_backend.calls}"
    )
    rows = json.loads(result.output)
    assert isinstance(rows, list), f"{' '.join(argv)}: expected an array, got {type(rows).__name__}"


def test_api_routes_maps_the_backend(inprocess_backend, cli_runner, isolated_home):
    """`api routes` is the discovery map every `api request` is written from."""
    result = _invoke(cli_runner, ["api", "routes"])

    assert result.exit_code == 0, result.output
    assert inprocess_backend.called("GET", "/api/routes"), inprocess_backend.calls
    rows = json.loads(result.output)
    assert rows, "the backend served no routes at all"
    assert all("rule" in row and "methods" in row for row in rows)


@pytest.mark.skip(
    reason="plugin-backed: /api/video-editor/health is an unconditional proxy to the "
    "video_editor plugin on 127.0.0.1:8207 (backend/api/video_editor_api.py "
    "`_proxy_get`), so it 503s unless that service is started -- and the e2e tier never "
    "starts a plugin service. `video-editor projects` above reads in-process state and "
    "is covered."
)
def test_video_editor_health_reports_the_service(inprocess_backend, cli_runner, isolated_home):
    data = _envelope(
        cli_runner, inprocess_backend, ["video-editor", "health"],
        ("GET", "/api/video-editor/health"),
    )

    assert "service" in data and "status" in data


@pytest.mark.skip(
    reason="plugin-backed: /api/upscaling/models answers 'Upscaling service not running' "
    "unless the upscaling service is started, and the e2e tier never starts a plugin "
    "service (it would be GPU work)."
)
def test_upscale_models_lists_installed_models(inprocess_backend, cli_runner, isolated_home):
    data = _envelope(
        cli_runner, inprocess_backend, ["upscale", "models"], ("GET", "/api/upscaling/models")
    )

    assert "models" in data
