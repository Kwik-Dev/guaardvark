"""start_plugin must not report success for a service that stopped answering.

PluginStatus.RUNNING is this process's memory. A service that crashed, or was
killed outside the manager, leaves the flag set, and the already-running
short-circuit used to return ``{'success': True}`` on the strength of it —
the Plugins page said started, nothing was listening.
"""

from types import SimpleNamespace

from backend.plugins.plugin_manager import PluginManager, PluginStatus


def _stub(plugin_type="service", answering=True, gate_open=False):
    metadata = SimpleNamespace(
        type=plugin_type,
        core=False,
        config=SimpleNamespace(enabled=True, service_url=None),
        endpoints={"health": "/health"},
        port=8206,
        dependencies=[],
    )
    broadcasts = []
    stub = SimpleNamespace(
        registry=SimpleNamespace(get_plugin=lambda pid: metadata),
        is_effectively_enabled=lambda pid: True,
        _plugin_status={"audio_foundry": PluginStatus.RUNNING},
        _broadcast_plugins_status=broadcasts.append,
        _check_service_running=lambda meta: answering,
        _save_running=lambda: None,
        _gate=SimpleNamespace(
            try_acquire=lambda pid: (True, 0.0, "") if gate_open else (False, 5.0, "cooling down")
        ),
    )
    return stub, broadcasts


def test_answering_service_still_short_circuits():
    stub, broadcasts = _stub(answering=True)

    result = PluginManager.start_plugin(stub, "audio_foundry")

    assert result == {"success": True, "message": "Plugin already running"}
    assert broadcasts == ["start:audio_foundry:already_running"]


def test_stale_running_flag_does_not_report_success():
    stub, _ = _stub(answering=False)

    result = PluginManager.start_plugin(stub, "audio_foundry")

    assert result["success"] is False
    assert result.get("message") != "Plugin already running"
    # The flag is corrected on the way past, so the page stops claiming it runs.
    assert stub._plugin_status["audio_foundry"] is PluginStatus.STOPPED


def test_non_service_plugin_is_not_health_probed():
    probed = []
    stub, _ = _stub(plugin_type="tool")
    stub._check_service_running = lambda meta: probed.append(meta) or False

    result = PluginManager.start_plugin(stub, "lora_trainer")

    assert result["success"] is True
    assert probed == [], "a tool has no server to probe"
