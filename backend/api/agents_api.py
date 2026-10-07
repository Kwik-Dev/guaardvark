#!/usr/bin/env python3
"""
Agents API
Exposes agent configuration and execution endpoints.
"""

import logging
from flask import Blueprint, request, jsonify
from typing import Dict, Any

from backend.services.agent_config import NO_AGENT_MATCHES

logger = logging.getLogger(__name__)

# Create blueprint
agents_bp = Blueprint("agents", __name__, url_prefix="/api/agents")


def _get_config_manager():
    """Get the agent config manager"""
    from backend.services.agent_config import get_agent_config_manager
    return get_agent_config_manager()


def _get_tool_registry():
    """Get the tool registry"""
    try:
        from backend.tools import initialize_all_tools
        return initialize_all_tools()
    except Exception as e:
        logger.error(f"Failed to get tool registry: {e}")
        return None


@agents_bp.route("", methods=["GET"])
@agents_bp.route("/", methods=["GET"])
def list_agents():
    """
    List all configured agents.

    Response:
    {
        "success": true,
        "agents": [
            {
                "id": "content_creator",
                "name": "Content Creator",
                "description": "...",
                "tools": [...],
                "enabled": true,
                "editable": ["enabled", "max_iterations", "system_prompt", "model"],
                "overridden": [],
                "group": "create",
                "summary": "SEO pages for WordPress",
                "tools_missing": [],
                "unavailable_reason": null
            }
        ]
    }
    """
    try:
        manager = _get_config_manager()
        registry = _get_tool_registry()
        agents = [manager.describe(agent, registry) for agent in manager.list_agents()]

        return jsonify({
            "success": True,
            "agents": agents,
            "count": len(agents)
        })

    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/<agent_id>", methods=["GET"])
def get_agent(agent_id: str):
    """
    Get a specific agent's configuration, with tools_detail for each of its tools.
    """
    try:
        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)

        if not agent:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent_id}' not found"
            }), 404

        return jsonify({
            "success": True,
            "agent": manager.describe(agent, _get_tool_registry(), detail=True)
        })

    except Exception as e:
        logger.error(f"Failed to get agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/<agent_id>", methods=["PATCH"])
def update_agent(agent_id: str):
    """
    Update an agent's editable fields (the agent's "editable" list).

    Request:
    {
        "enabled": true,
        "max_iterations": 15,          # 1-50
        "system_prompt": "...",        # non-empty, up to 20,000 characters
        "model": "name:tag" | null     # null or "" runs on the active chat model
    }

    A field the agent does not allow, or a value out of range, is a 400 that
    names the problem and lists the editable fields; nothing is changed then.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)
        if not agent:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent_id}' not found"
            }), 404

        try:
            manager.update_agent(agent_id, data)
        except ValueError as e:
            return jsonify({
                "success": False,
                "error": str(e),
                "editable": list(agent.editable),
            }), 400

        return jsonify({
            "success": True,
            "message": f"Agent '{agent_id}' updated",
            "agent": manager.describe(agent, _get_tool_registry(), detail=True)
        })

    except Exception as e:
        logger.error(f"Failed to update agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/<agent_id>/reset", methods=["POST"])
def reset_agent(agent_id: str):
    """
    Put an agent's fields back to the built-in default.

    Request (optional): {"fields": ["system_prompt", "max_iterations", "model"]}
    With no fields, the instructions, iteration limit and model are reset; the
    on/off switch is kept.
    """
    try:
        data = request.get_json(silent=True) or {}
        fields = data.get("fields") if isinstance(data, dict) else None
        if fields is not None and (
            not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
        ):
            return jsonify({"success": False, "error": "fields must be a list of field names"}), 400

        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)
        if not agent:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent_id}' not found"
            }), 404

        try:
            manager.reset_agent(agent_id, fields)
        except ValueError as e:
            return jsonify({
                "success": False,
                "error": str(e),
                "editable": list(agent.editable),
            }), 400

        return jsonify({
            "success": True,
            "message": f"Agent '{agent_id}' reset to default",
            "agent": manager.describe(agent, _get_tool_registry(), detail=True)
        })

    except Exception as e:
        logger.error(f"Failed to reset agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/<agent_id>/toggle", methods=["POST"])
def toggle_agent(agent_id: str):
    """
    Toggle an agent's enabled status.
    """
    try:
        manager = _get_config_manager()
        agent = manager.get_agent(agent_id)

        if not agent:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent_id}' not found"
            }), 404

        new_status = not agent.enabled
        manager.set_agent_enabled(agent_id, new_status)

        return jsonify({
            "success": True,
            "agent_id": agent_id,
            "enabled": new_status,
            "agent": manager.describe(agent, _get_tool_registry()),
        })

    except Exception as e:
        logger.error(f"Failed to toggle agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/match", methods=["POST"])
def match_agent():
    """
    Find the best agent for a given message.

    Request:
    {
        "message": "Generate 50 WordPress pages about SEO"
    }

    Response:
    {
        "success": true,
        "agent": {...},
        "matched_pattern": "wordpress"
    }
    """
    try:
        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({
                "success": False,
                "error": "message is required"
            }), 400

        message = data["message"]
        manager = _get_config_manager()
        agent = manager.get_agent_for_message(message)

        if agent:
            return jsonify({
                "success": True,
                "agent": agent.to_dict(),
                "agent_id": agent.id
            })
        else:
            return jsonify({
                "success": True,
                "agent": None,
                "message": NO_AGENT_MATCHES
            })

    except Exception as e:
        logger.error(f"Failed to match agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/execute", methods=["POST"])
def execute_agent():
    """
    Execute an agent with a user query.

    Request:
    {
        "agent_id": "content_creator",
        "message": "Generate 10 WordPress pages about digital marketing",
        "context": {}
    }

    Response:
    {
        "success": true,
        "result": {...}
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        agent_id = data.get("agent_id")
        message = data.get("message")
        context = data.get("context", {})

        if not message:
            return jsonify({
                "success": False,
                "error": "message is required"
            }), 400

        manager = _get_config_manager()

        # Get agent (or match one if not specified)
        if agent_id:
            agent = manager.get_agent(agent_id)
            if not agent:
                return jsonify({
                    "success": False,
                    "error": f"Agent '{agent_id}' not found"
                }), 404
        else:
            agent = manager.get_agent_for_message(message)
            if not agent:
                return jsonify({
                    "success": False,
                    "error": NO_AGENT_MATCHES
                }), 404

        if not agent.enabled:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent.id}' is disabled"
            }), 400

        # Orchestrator agents delegate through the OrchestratorService (their
        # "delegate_task" tool is not a registry tool), as agent_router does.
        from backend.services.agent_config import AgentType
        if agent.agent_type == AgentType.ORCHESTRATOR:
            return jsonify({
                "success": False,
                "error": (f"Agent '{agent.id}' is an orchestrator; run it through "
                          f"/api/orchestrator or the chat instead of /execute"),
            }), 400

        # Get tool registry
        tool_registry = _get_tool_registry()
        if not tool_registry:
            return jsonify({
                "success": False,
                "error": "Tool registry not available"
            }), 500

        agent_tool_registry, missing = tool_registry.subset(agent.tools)
        if missing:
            logger.warning(f"Agent '{agent.id}' references tools that are not available: {missing}")

        if len(agent_tool_registry) == 0:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent.id}' has no available tools"
            }), 400

        # Execute using agent executor
        try:
            from backend.services.agent_executor import AgentExecutor
            from backend.utils.llm_service import get_default_llm

            llm = get_default_llm()
            executor = AgentExecutor(
                agent_tool_registry, llm, max_iterations=agent.max_iterations, agent=agent
            )
            from backend.services.agent_prompt_blocks import user_context_line
            result = executor.execute(message, session_context=user_context_line(context))

            return jsonify({
                "success": True,
                "agent_used": agent.id,
                "result": {
                    "final_answer": result.final_answer,
                    "iterations": result.iterations,
                    "success": result.success,
                    "error": result.error,
                    "steps": [
                        {
                            "iteration": s.iteration,
                            "thoughts": s.thoughts,
                            "tool_calls": s.tool_calls if isinstance(s.tool_calls, list) else (
                                [tc if isinstance(tc, dict) else tc.dict() if hasattr(tc, 'dict') else str(tc) 
                                 for tc in s.tool_calls] if s.tool_calls else []
                            ),
                            "observations": s.observations if hasattr(s, 'observations') else [],
                        }
                        for s in result.steps
                    ]
                }
            })

        except Exception as exec_error:
            logger.error(f"Agent execution failed: {exec_error}", exc_info=True)
            return jsonify({
                "success": False,
                "error": f"Agent execution failed: {str(exec_error)}"
            }), 500

    except Exception as e:
        logger.error(f"Failed to execute agent: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/settings", methods=["GET"])
def get_agent_settings():
    """
    Get global agent system settings (persisted to DB).
    """
    try:
        from backend.utils.settings_utils import get_setting

        settings = {
            "agent_routing_enabled": get_setting("agent_routing_enabled", default=False, cast=bool),
            "default_max_iterations": 10,
            "fallback_to_chat": True,
            "log_agent_actions": get_setting("log_agent_actions", default=True, cast=bool),
        }

        return jsonify({
            "success": True,
            "settings": settings
        })

    except Exception as e:
        logger.error(f"Failed to get settings: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@agents_bp.route("/settings", methods=["PATCH"])
def update_agent_settings():
    """
    Update global agent system settings (persisted to DB).
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "Request body required"
            }), 400

        from backend.utils.settings_utils import save_setting

        if "agent_routing_enabled" in data:
            save_setting("agent_routing_enabled", str(data["agent_routing_enabled"]).lower())

        if "log_agent_actions" in data:
            save_setting("log_agent_actions", str(data["log_agent_actions"]).lower())

        return jsonify({
            "success": True,
            "message": "Settings updated"
        })

    except Exception as e:
        logger.error(f"Failed to update settings: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
