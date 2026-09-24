#!/usr/bin/env python3
"""
Automation API
REST endpoints for managing browser, desktop, and MCP automation services.
"""

import asyncio
import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

automation_bp = Blueprint("automation", __name__, url_prefix="/api/automation")


def _run_async(coro):
    """Helper to run async code from sync Flask context"""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=60)
    except RuntimeError:
        return asyncio.run(coro)


# ==================== Status Endpoints ====================

@automation_bp.route("/status", methods=["GET"])
def get_automation_status():
    """Get status of all automation services"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        from backend.services.desktop_automation_service import get_desktop_service
        from backend.services.mcp_client_service import get_mcp_service
        
        browser_state = get_browser_service().get_state()
        desktop_state = get_desktop_service().get_state()
        mcp_state = get_mcp_service().get_state()
        
        return jsonify({
            "success": True,
            "services": {
                "browser": browser_state,
                "desktop": desktop_state,
                "mcp": mcp_state
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting automation status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Browser Endpoints ====================

@automation_bp.route("/browser/status", methods=["GET"])
def get_browser_status():
    """Get browser automation service status"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        state = get_browser_service().get_state()
        return jsonify({"success": True, **state})
    except Exception as e:
        logger.error(f"Error getting browser status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/start", methods=["POST"])
def start_browser():
    """Start the browser automation service"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        service = get_browser_service()
        result = _run_async(service._start_browser())
        
        if result:
            return jsonify({
                "success": True,
                "message": "Browser started",
                "state": service.get_state()
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to start browser",
                "state": service.get_state()
            }), 500
            
    except Exception as e:
        logger.error(f"Error starting browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/stop", methods=["POST"])
def stop_browser():
    """Stop the browser automation service"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        service = get_browser_service()
        _run_async(service.shutdown())

        # shutdown() best-effort-closes pages/context/browser and sets
        # initialized=False, active_pages=0 only if it actually completed. Report
        # success from the REAL post-state, not unconditionally.
        state = service.get_state()
        stopped = (not state.get("initialized")) and state.get("active_pages", 0) == 0
        if stopped:
            return jsonify({
                "success": True,
                "message": "Browser stopped",
                "state": state
            })
        return jsonify({
            "success": False,
            "error": "Browser did not fully stop",
            "state": state
        }), 500
        
    except Exception as e:
        logger.error(f"Error stopping browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/navigate", methods=["POST"])
def browser_navigate():
    """Navigate browser to a URL"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        data = request.get_json() or {}
        url = data.get("url")
        wait_for = data.get("wait_for", "load")
        
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400
        
        service = get_browser_service()
        result = _run_async(service.navigate(url, wait_for=wait_for))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error navigating browser: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/browser/screenshot", methods=["POST"])
def browser_screenshot():
    """Take a browser screenshot"""
    try:
        from backend.services.browser_automation_service import get_browser_service
        
        data = request.get_json() or {}
        url = data.get("url")
        full_page = data.get("full_page", False)
        selector = data.get("selector")
        format = data.get("format", "png")
        
        if not url:
            return jsonify({"success": False, "error": "URL is required"}), 400
        
        service = get_browser_service()
        result = _run_async(service.screenshot(url, full_page=full_page, selector=selector, format=format))
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error taking screenshot: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== Desktop Endpoints ====================

@automation_bp.route("/desktop/status", methods=["GET"])
def get_desktop_status():
    """Get desktop automation service status"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        state = get_desktop_service().get_state()
        return jsonify({"success": True, **state})
    except Exception as e:
        logger.error(f"Error getting desktop status: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/allowed-paths", methods=["GET"])
def get_allowed_paths():
    """Get allowed file operation paths"""
    try:
        from backend.services.desktop_automation_service import ALLOWED_PATHS
        return jsonify({
            "success": True,
            "allowed_paths": ALLOWED_PATHS
        })
    except Exception as e:
        logger.error(f"Error getting allowed paths: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/allowed-apps", methods=["GET"])
def get_allowed_apps():
    """Get allowed applications"""
    try:
        from backend.services.desktop_automation_service import ALLOWED_APPS
        return jsonify({
            "success": True,
            "allowed_apps": ALLOWED_APPS
        })
    except Exception as e:
        logger.error(f"Error getting allowed apps: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/audit-log", methods=["GET"])
def get_desktop_audit_log():
    """Get desktop automation audit log"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        limit = request.args.get("limit", 100, type=int)
        log = get_desktop_service().get_audit_log(limit)
        
        return jsonify({
            "success": True,
            "entries": log,
            "count": len(log)
        })
        
    except Exception as e:
        logger.error(f"Error getting audit log: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/file-watchers", methods=["GET"])
def get_file_watchers():
    """Get active file watchers"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        service = get_desktop_service()
        watchers = []
        
        for watch_id, watcher in service._file_watchers.items():
            watchers.append({
                "watch_id": watch_id,
                "path": watcher.path,
                "events": watcher.events,
                "created_at": watcher.created_at.isoformat(),
                "event_count": watcher.event_count
            })
        
        return jsonify({
            "success": True,
            "watchers": watchers,
            "count": len(watchers)
        })
        
    except Exception as e:
        logger.error(f"Error getting file watchers: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@automation_bp.route("/desktop/notification", methods=["POST"])
def send_notification():
    """Send a desktop notification"""
    try:
        from backend.services.desktop_automation_service import get_desktop_service
        
        data = request.get_json() or {}
        title = data.get("title")
        message = data.get("message")
        timeout = data.get("timeout", 10)
        
        if not title or not message:
            return jsonify({"success": False, "error": "title and message are required"}), 400
        
        service = get_desktop_service()
        result = service.notification_send(title, message, timeout=timeout)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ==================== MCP Endpoints ====================
# Config writes (/mcp/servers/<name>, /mcp/reload-config) are always behind
# auth_guard; the rest are when GUAARDVARK_PROTECT_TOOL_ENDPOINTS is on. A
# caller on this machine (or holding the API key) calling /mcp/execute is the
# approval for policy-gated tools; any other caller can run only the tools the
# policy allows. Every call is still subject to denyTools and audited.


def _caller_is_trusted() -> bool:
    import hmac
    import os

    from backend.utils.auth_guard import _effective_client_ip, _is_localhost

    api_key = os.environ.get("GUAARDVARK_API_KEY")
    if api_key:
        provided = request.headers.get("X-API-Key", "")
        return bool(provided) and hmac.compare_digest(provided, api_key)
    return _is_localhost(_effective_client_ip())

def _mcp():
    from backend.services.mcp_client_service import get_mcp_service
    return get_mcp_service()


def _mcp_response(result, ok_status=200):
    if result.get("success"):
        return jsonify(result), ok_status
    error = (result.get("error") or "").lower()
    status = 404 if error.startswith("unknown server") or "unknown tool" in error else 400
    if "disabled" in error or "not installed" in error:
        status = 503
    return jsonify(result), status


def _mcp_route(fn):
    """Uniform error handling for MCP endpoints."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"MCP endpoint {request.path} failed: {e}", exc_info=True)
            return jsonify({"success": False, "error": f"Internal error: {type(e).__name__}"}), 500

    return wrapper


@automation_bp.route("/mcp/status", methods=["GET"])
@_mcp_route
def get_mcp_status():
    """Get MCP client service status"""
    return jsonify({"success": True, **_mcp().get_state()})


@automation_bp.route("/mcp/servers", methods=["GET"])
@_mcp_route
def list_mcp_servers():
    """List configured MCP servers (secrets redacted)"""
    return jsonify(_mcp().list_configured_servers())


@automation_bp.route("/mcp/servers/<name>", methods=["GET"])
@_mcp_route
def get_mcp_server(name):
    """Server detail: capabilities, tools with schemas and policy, stderr tail"""
    return _mcp_response(_mcp().get_server(name))


@automation_bp.route("/mcp/servers/<name>", methods=["PUT"])
@_mcp_route
def upsert_mcp_server(name):
    """Create or replace a server in data/config/mcp_servers.json.

    Body uses the mcpServers entry format. For env/headers, send "***" (or an
    empty value) to keep a stored secret unchanged.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "JSON object body required"}), 400
    return _mcp_response(_mcp().upsert_server(name, data))


@automation_bp.route("/mcp/servers/<name>", methods=["DELETE"])
@_mcp_route
def delete_mcp_server(name):
    """Remove a server from the config file (disconnecting it first)"""
    return _mcp_response(_mcp().remove_server(name))


@automation_bp.route("/mcp/reload-config", methods=["POST"])
@_mcp_route
def reload_mcp_config():
    """Re-read config; servers whose definition changed are disconnected"""
    return jsonify(_mcp().reload_config())


@automation_bp.route("/mcp/connect", methods=["POST"])
@_mcp_route
def connect_mcp_server():
    """Connect to an MCP server"""
    server_name = (request.get_json(silent=True) or {}).get("server")
    if not server_name:
        return jsonify({"success": False, "error": "server name is required"}), 400
    return _mcp_response(_mcp().connect(server_name))


@automation_bp.route("/mcp/disconnect", methods=["POST"])
@_mcp_route
def disconnect_mcp_server():
    """Disconnect from an MCP server"""
    server_name = (request.get_json(silent=True) or {}).get("server")
    if not server_name:
        return jsonify({"success": False, "error": "server name is required"}), 400
    return _mcp_response(_mcp().disconnect(server_name))


@automation_bp.route("/mcp/tools", methods=["GET"])
@_mcp_route
def list_mcp_tools():
    """List tools from connected MCP servers, with policy"""
    return _mcp_response(_mcp().list_tools(request.args.get("server") or None))


@automation_bp.route("/mcp/execute", methods=["POST"])
@_mcp_route
def execute_mcp_tool():
    """Execute a tool on an MCP server; a trusted caller is the approving human."""
    data = request.get_json(silent=True) or {}
    server = data.get("server")
    tool = data.get("tool")
    arguments = data.get("arguments")
    arguments = {} if arguments is None else arguments
    if not server or not tool:
        return jsonify({"success": False, "error": "server and tool are required"}), 400
    if not isinstance(arguments, dict):
        return jsonify({"success": False, "error": "arguments must be a JSON object"}), 400
    caller = str(data.get("caller") or "rest")[:20]
    result = _mcp().call_tool(server, tool, arguments, approved=_caller_is_trusted(), caller=caller)
    return _mcp_response(result)


@automation_bp.route("/mcp/resources", methods=["GET"])
@_mcp_route
def list_mcp_resources():
    return _mcp_response(_mcp().list_resources(request.args.get("server") or None))


@automation_bp.route("/mcp/resources/read", methods=["POST"])
@_mcp_route
def read_mcp_resource():
    data = request.get_json(silent=True) or {}
    if not data.get("server") or not data.get("uri"):
        return jsonify({"success": False, "error": "server and uri are required"}), 400
    return _mcp_response(_mcp().read_resource(data["server"], str(data["uri"]), caller="rest"))


@automation_bp.route("/mcp/prompts", methods=["GET"])
@_mcp_route
def list_mcp_prompts():
    return _mcp_response(_mcp().list_prompts(request.args.get("server") or None))


@automation_bp.route("/mcp/prompts/get", methods=["POST"])
@_mcp_route
def get_mcp_prompt():
    data = request.get_json(silent=True) or {}
    args = data.get("arguments")
    args = {} if args is None else args
    if not data.get("server") or not data.get("name") or not isinstance(args, dict):
        return jsonify({"success": False, "error": "server, name and an object of arguments are required"}), 400
    return _mcp_response(_mcp().get_prompt(data["server"], data["name"], args))


@automation_bp.route("/mcp/audit-log", methods=["GET"])
@_mcp_route
def get_mcp_audit_log():
    limit = request.args.get("limit", 100, type=int)
    entries = _mcp().get_audit_log(limit)
    return jsonify({"success": True, "entries": entries, "count": len(entries)})
