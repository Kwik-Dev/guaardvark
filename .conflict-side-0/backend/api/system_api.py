
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, request
from backend.utils.response_utils import success_response, error_response

logger = logging.getLogger(__name__)

system_bp = Blueprint("system", __name__, url_prefix="/api/system")


def _read_version() -> str:
    """Version from the repo-root VERSION file, the same source app.py uses.

    Resolved relative to this file rather than GUAARDVARK_ROOT, which may point
    at a writable data directory instead of the checkout.
    """
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


@system_bp.route("/version", methods=["GET"])
def get_version():
    try:
        return success_response({
            "version": _read_version(),
            "name": "guaardvark",
            "description": "LLM-powered development environment",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, "Version retrieved")
    except Exception as e:
        logger.error(f"Error getting version: {e}")
        return error_response(str(e), 500, "VERSION_ERROR")


@system_bp.route("/cleanup-progress-jobs", methods=["POST"])
def cleanup_progress_jobs():
    try:
        data = request.get_json() or {}
        execute = data.get("execute", False)
        
        script_path = Path(__file__).parent.parent.parent / "scripts" / "cleanup_stuck_progress_jobs.py"
        
        if not script_path.exists():
            return error_response("Cleanup script not found", 404, "SCRIPT_NOT_FOUND")
        
        cmd = [sys.executable, str(script_path)]
        if execute:
            cmd.append("--execute")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.error(f"Cleanup script failed: {result.stderr}")
            return error_response("Cleanup script failed", 500, "SCRIPT_FAILED")
        
        output_lines = result.stdout.split('\n')
        cleaned_count = 0
        
        for line in output_lines:
            if "Cleaned up" in line and "orphaned jobs" in line:
                try:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part.isdigit() and i + 1 < len(parts) and "orphaned" in parts[i + 1]:
                            cleaned_count = int(part)
                            break
                except (ValueError, IndexError):
                    pass
        
        return success_response({
            "cleaned_count": cleaned_count,
            "output": result.stdout,
            "executed": execute
        }, "Cleanup completed")
        
    except subprocess.TimeoutExpired:
        return error_response("Cleanup script timed out", 500, "TIMEOUT")
    except Exception as e:
        logger.error(f"Error running cleanup script: {e}")
        return error_response(str(e), 500, "CLEANUP_ERROR")


@system_bp.route("/health-check", methods=["GET"])
def system_health_check():
    try:
        from datetime import datetime, timezone

        components = {}

        # Component 1: unified progress system — cheap in-process probe (no I/O):
        # if we can fetch the active-process snapshot, it's operational.
        try:
            from backend.utils.unified_progress_system import get_unified_progress
            get_unified_progress().get_active_processes()
            components["progress_system"] = "operational"
        except Exception as e:
            logger.warning(f"progress_system health probe failed: {e}")
            components["progress_system"] = "unavailable"

        # Component 2: cleanup script — it's invoked via subprocess by
        # /cleanup-progress-jobs, so "available" means the file actually exists.
        cleanup_script = Path(__file__).parent.parent.parent / "scripts" / "cleanup_stuck_progress_jobs.py"
        components["cleanup_script"] = "available" if cleanup_script.exists() else "missing"

        # Healthy only if every probed component is in its good state.
        good = {"progress_system": "operational", "cleanup_script": "available"}
        status = "healthy" if all(components[k] == v for k, v in good.items()) else "degraded"

        # NOTE: success_response signature is (data, message) — pass by keyword so the
        # health payload lands in `data`, not `message` (the old positional call had
        # these swapped, burying the real status in the message field).
        return success_response(
            data={
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "components": components,
            },
            message="System health check completed",
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return error_response(str(e), 500, "HEALTH_CHECK_ERROR")
