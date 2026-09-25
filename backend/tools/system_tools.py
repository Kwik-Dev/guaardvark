#!/usr/bin/env python3
"""
System Tools
Safe, read-only system operations for agents.
"""

import logging
import subprocess
import shlex
from typing import List, Dict, Any

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

class SystemCommandTool(BaseTool):
    """
    Executes safe, read-only system commands.
    """
    
    name = "system_command"
    description = "Inspect local project files and directories (ls, grep, cat, find). Only for filesystem operations, NOT for looking up general information — use web_search for that."
    is_dangerous = False # Explicitly safe because of whitelist
    requires_approval = True
    
    # Whitelist of allowed commands
    ALLOWED_COMMANDS = [
        "ls", "grep", "cat", "find", "wc", "head", "tail", "pwd", "whoami", "date", "echo"
    ]
    
    parameters = {
        "command": ToolParameter(
            name="command",
            type="string",
            required=True,
            description="The command to execute (e.g., 'ls -la', 'grep pattern file.txt')"
        ),
        "cwd": ToolParameter(
            name="cwd",
            type="string",
            required=False,
            description="Current working directory",
            default=None
        )
    }
    
    # find(1) expressions that execute programs, delete or write files.
    FIND_FORBIDDEN = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
                      "-fprintf", "-fls"}
    # Options whose next argument is a value, not a path.
    VALUE_OPTIONS = {
        "grep": {"-m", "--max-count", "-A", "-B", "-C", "--after-context", "--before-context",
                 "--context", "--label", "--color", "--colour", "-d", "-D"},
        "head": {"-n", "-c", "--lines", "--bytes"},
        "tail": {"-n", "-c", "--lines", "--bytes"},
        "ls": {"-I", "--ignore", "--hide", "-w", "--width", "-T", "--tabsize", "--sort", "--time-style"},
    }
    NO_PATH_COMMANDS = {"pwd", "whoami", "date", "echo"}

    @staticmethod
    def _allowed_roots():
        from backend import config

        return [str(config.GUAARDVARK_ROOT)] + list(getattr(config, "ALLOWED_AUTOMATION_PATHS", []))

    def _path_args(self, base_cmd, args):
        """The args that name files or directories (checked for credential files
        and, when paths are confined, for containment)."""
        paths, skip_next, pattern_seen = [], False, False
        value_opts = self.VALUE_OPTIONS.get(base_cmd, set())
        find_expr = False
        for i, arg in enumerate(args):
            if skip_next:
                skip_next = False
                continue
            if base_cmd == "find":
                if find_expr or arg.startswith("-") or arg in ("(", ")", "!", ","):
                    find_expr = True  # everything after the first expression token is expression
                    continue
                paths.append(arg)
                continue
            if base_cmd == "grep":
                if arg in ("-e", "--regexp"):
                    pattern_seen, skip_next = True, True
                    continue
                if arg in ("-f", "--file"):
                    pattern_seen = True
                    if i + 1 < len(args):
                        paths.append(args[i + 1])
                    skip_next = True
                    continue
            if arg in value_opts:
                skip_next = True
                continue
            if arg.startswith("-"):
                continue
            if base_cmd == "grep" and not pattern_seen:
                pattern_seen = True  # first positional is the pattern
                continue
            paths.append(arg)
        return paths

    def execute(self, **kwargs) -> ToolResult:
        """Execute the system command"""
        from backend.utils.path_safety import is_sensitive

        command_str = kwargs.get("command", "")
        cwd = kwargs.get("cwd")
        
        if not command_str:
            return ToolResult(success=False, error="Command is required")
            
        # Security check: Parse command and check against whitelist
        try:
            parts = shlex.split(command_str)
            if not parts:
                return ToolResult(success=False, error="Empty command")
                
            base_cmd = parts[0]
            if base_cmd not in self.ALLOWED_COMMANDS:
                return ToolResult(
                    success=False, 
                    error=f"Command '{base_cmd}' is not allowed. Allowed: {', '.join(self.ALLOWED_COMMANDS)}"
                )
                
            # Prevent chaining or piping which might bypass checks (simple check)
            if any(c in command_str for c in [";", "|", "&", "`", "$("]):
                 return ToolResult(
                    success=False, 
                    error="Command chaining/piping/substitution is not allowed for security reasons."
                )

            if base_cmd == "find":
                bad = [a for a in parts[1:] if a in self.FIND_FORBIDDEN or a.startswith("-fprint")]
                if bad:
                    return ToolResult(
                        success=False,
                        error=f"find actions that run programs or modify files are not allowed: {', '.join(bad)}",
                    )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to parse command: {e}")

        from backend.utils.settings_utils import get_confine_tool_paths

        if get_confine_tool_paths():
            # Settings → Agents → "Project folder only": the working directory
            # and every path argument stay inside the project and
            # GUAARDVARK_ALLOWED_PATHS.
            from backend import config
            from backend.utils.path_safety import is_within

            roots = self._allowed_roots()
            cwd = cwd or str(config.GUAARDVARK_ROOT)
            if not is_within(cwd, roots):
                return ToolResult(success=False, error=f"cwd '{cwd}' is outside the allowed directories")
            if base_cmd not in self.NO_PATH_COMMANDS:
                for path in self._path_args(base_cmd, parts[1:]):
                    if not is_within(path, roots, base=cwd):
                        return ToolResult(
                            success=False,
                            error=(f"'{path}' is outside the project folder and GUAARDVARK_ALLOWED_PATHS "
                                   f"(Settings → Agents → Project folder only is on)"),
                        )

        if base_cmd not in self.NO_PATH_COMMANDS:
            for path in self._path_args(base_cmd, parts[1:]):
                if is_sensitive(path):
                    return ToolResult(success=False, error=f"'{path}' may contain credentials and cannot be read")

        if base_cmd == "grep":
            # Recursive greps must not print secrets from credential files.
            from backend.utils.path_safety import SENSITIVE_PATTERNS

            parts = parts[:1] + [f"--exclude={p}" for p in SENSITIVE_PATTERNS] + ["--exclude-dir=.git"] + parts[1:]

        try:
            # Use project root as default CWD if available in context
            if not cwd and self._context:
                # Try to get project path from context if available
                # This is a placeholder for where we'd use the injected context
                pass
                
            # Execute
            result = subprocess.run(
                parts, 
                capture_output=True, 
                text=True, 
                cwd=cwd,
                timeout=10
            )
            
            return ToolResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                metadata={
                    "returncode": result.returncode,
                    "command": command_str
                }
            )
            
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Command timed out")
        except Exception as e:
            logger.error(f"System command execution failed: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e))
