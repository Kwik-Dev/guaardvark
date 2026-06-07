"""Route-intent → plugin-lifecycle bridge.

The documented missing wire (CLAUDE.md "plugin auto-orchestration goal"): a
feature that needs a plugin should be able to bring it up on demand. This is the
minimal, focused version of that — NOT a rewrite of gpu_memory_orchestrator
(which is load-bearing and whose preload path is deliberately left alone).

`ensure_plugin_running` reuses the existing, already-gated, already-idempotent
PluginManager primitives (`is_effectively_enabled` / `enable_plugin` /
`start_plugin`). It is the single call a Celery stage task makes to guarantee a
plugin is up before using it.

CUDA-FORK SAFETY (load-bearing — see plugin_runner.py docstring): this MUST be
called from INSIDE a task/request body at runtime, never at import time. The
underlying start_plugin runs the plugin's start script through the plugin_runner
sidecar precisely so the backend never fork()s after importing torch.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginUnavailable(RuntimeError):
    """A required plugin could not be enabled/started. Stage tasks turn this into
    a clean fail_stage rather than a crash."""


def ensure_plugin_running(plugin_id: str, *, enable_if_disabled: bool = True) -> None:
    """Make sure ``plugin_id`` is enabled and running. Raises PluginUnavailable otherwise.

    Idempotent: start_plugin short-circuits to success when the plugin is already
    RUNNING, and only commits status once its health endpoint answers. On gate
    contention (e.g. comfyui vs ollama GPU-exclusivity, or a cooldown) the
    rejection is surfaced verbatim so the caller can fail the stage with a useful
    message instead of hanging.
    """
    from backend.plugins.plugin_manager import get_plugin_manager

    pm = get_plugin_manager()

    # video_editor ships default_enabled=false, and start_plugin gates on
    # metadata.config.enabled — so enable first (this also flips config.enabled
    # in memory and persists the user_enabled overlay). enable_plugin is a no-op
    # cost when already enabled; we only call it when needed.
    if not pm.is_effectively_enabled(plugin_id):
        if not enable_if_disabled:
            raise PluginUnavailable(f"plugin '{plugin_id}' is disabled")
        res = pm.enable_plugin(plugin_id)
        if not res.get("success"):
            raise PluginUnavailable(f"could not enable '{plugin_id}': {res.get('error')}")
        logger.info("plugin_bridge: enabled '%s' on demand", plugin_id)

    res = pm.start_plugin(plugin_id)
    if not res.get("success"):
        detail = res.get("error") or "unknown error"
        cooldown = res.get("cooldown_remaining")
        if cooldown:
            detail = f"{detail} (retry in ~{cooldown:.0f}s)"
        raise PluginUnavailable(f"could not start '{plugin_id}': {detail}")
    logger.info("plugin_bridge: '%s' running (%s)", plugin_id, res.get("message", "started"))
