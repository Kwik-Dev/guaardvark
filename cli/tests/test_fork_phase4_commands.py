"""Phase 4: the llm provider group and wordpress.

The cloud switch is the one that matters. `llm cloud on` decides whether the user's
prompts leave the machine, so these tests pin the gate: it must refuse without --yes, it
must not call the backend when it refuses, and — since the switch has no generic settings
route — that refusal is the only thing standing between a script and the user's prompts.
"""
from __future__ import annotations

import json

from llx.main import app


def _run(cli_runner, args):
    result = cli_runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


# --- llm -------------------------------------------------------------------


def test_llm_provider_reports_state(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/llm/provider", json={
        "success": True,
        "data": {"provider": "ollama", "cloud_models_enabled": False, "cloud_active": False},
    })

    payload = _run(cli_runner, ["llm", "provider", "--json"])

    assert payload["data"]["provider"] == "ollama"


def test_llm_set_posts_the_provider(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/llm/provider", json={"success": True, "data": {"provider": "mistral"}})

    _run(cli_runner, ["llm", "set", "mistral", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/llm/provider")[0][2])
    assert body == {"provider": "mistral"}


def test_llm_cloud_on_refuses_without_yes_and_touches_nothing(fake_backend, cli_runner, isolated_home):
    """The gate that stops a script sending the user's prompts to a third party.

    There is no generic /api/settings/<key> route, so `guaardvark settings set` cannot
    reach this switch — this refusal is the only thing in front of it.
    """
    result = cli_runner.invoke(app, ["llm", "cloud", "on", "--json"])

    assert result.exit_code == 2
    assert "CONFIRMATION_REQUIRED" in (result.output or "")
    assert fake_backend.calls == []


def test_llm_cloud_on_with_yes_enables(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/llm/cloud-enabled", json={
        "success": True, "data": {"provider": "mistral", "cloud_models_enabled": True},
    })

    _run(cli_runner, ["llm", "cloud", "on", "--yes", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/llm/cloud-enabled")[0][2])
    assert body == {"enabled": True}


def test_llm_cloud_off_needs_no_confirmation(fake_backend, cli_runner, isolated_home):
    """The safe direction is free — refusing to turn it off would be perverse."""
    fake_backend.route("POST", "/api/llm/cloud-enabled", json={"success": True, "data": {}})

    result = cli_runner.invoke(app, ["llm", "cloud", "off", "--json"])

    assert result.exit_code == 0, result.output
    body = json.loads(fake_backend.calls_for("POST", "/api/llm/cloud-enabled")[0][2])
    assert body == {"enabled": False}


def test_llm_cloud_rejects_a_typo_in_the_state(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["llm", "cloud", "true", "--json"])

    assert result.exit_code == 2
    assert "BAD_ARGUMENT" in (result.output or "")
    assert fake_backend.calls == []


def test_llm_test_calls_the_round_trip(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/llm/provider/test", json={"success": True, "data": {"ok": True}})

    _run(cli_runner, ["llm", "test", "--json"])

    assert fake_backend.calls_for("POST", "/api/llm/provider/test")


# --- audio models (extension of the upstream audio group) ------------------


def test_audio_models_lists_audio_foundry_models(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/audio-foundry/models", json={
        "success": True, "data": {"models": [{"id": "ace-step", "installed": True}]},
    })

    payload = _run(cli_runner, ["audio", "models", "--json"])

    assert payload["data"]["models"][0]["id"] == "ace-step"


def test_audio_model_download_gates_gigabytes(fake_backend, cli_runner, isolated_home):
    result = cli_runner.invoke(app, ["audio", "model-download", "ace-step", "--json"])

    assert result.exit_code == 2
    assert fake_backend.calls == []


# --- wordpress -------------------------------------------------------------


def test_wordpress_sites(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/wordpress/sites", json={
        "sites": [{"id": 1, "name": "Blog", "url": "https://example.test", "is_connected": True}],
    })

    payload = _run(cli_runner, ["wordpress", "sites", "--json"])

    assert payload["data"]["sites"][0]["name"] == "Blog"


def test_wordpress_pull_sitemap_posts_the_site_id(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/wordpress/pull/sitemap", json={"success": True, "data": {}})

    _run(cli_runner, ["wordpress", "pull-sitemap", "--site", "1", "--json"])

    body = json.loads(fake_backend.calls_for("POST", "/api/wordpress/pull/sitemap")[0][2])
    assert body == {"site_id": 1}


def test_wordpress_process_run_gates_the_outbound_publish(fake_backend, cli_runner, isolated_home):
    """Executing the queue publishes to a live site."""
    result = cli_runner.invoke(app, ["wordpress", "process-run", "--json"])

    assert result.exit_code == 2
    assert "CONFIRMATION_REQUIRED" in (result.output or "")
    assert fake_backend.calls == []


def test_wordpress_reads_are_not_gated(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/wordpress/pull/status/1", json={"success": True, "data": {"state": "idle"}})

    _run(cli_runner, ["wordpress", "pull-status", "1", "--json"])

    assert fake_backend.calls_for("GET", "/api/wordpress/pull/status/1")
