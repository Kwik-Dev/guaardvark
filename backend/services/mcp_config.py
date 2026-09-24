"""MCP server configuration: parsing, validation, persistence, child env.

Accepted shapes (file ``data/config/mcp_servers.json`` or env
``GUAARDVARK_MCP_SERVERS``):

Claude-Desktop style (preferred)::

    {"mcpServers": {"fs": {"command": "npx",
                           "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/x"],
                           "env": {"FOO": "bar"}, "autoConnect": true}}}

Legacy flat map, where ``command`` may be a list::

    {"fs": {"command": ["npx", "-y", "..."]}}

Only local stdio servers are supported: each server is a program started
on this machine. Remote (HTTP) MCP servers are rejected. ``${VAR}`` references
in ``env`` values are resolved from the backend environment at connect time so
secrets never need to be written into the config file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Environment variables passed through to stdio MCP servers. Everything else
# (DATABASE_URL, API keys, Celery/Redis URLs, ...) is withheld unless a server
# explicitly lists it in its own "env" block.
ENV_PASSTHROUGH_EXACT = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LANGUAGE",
    "TMPDIR", "TEMP", "TMP", "TZ", "VIRTUAL_ENV", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "SYSTEMROOT",
    "NVM_DIR", "NVM_BIN", "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}
ENV_PASSTHROUGH_PREFIXES = ("LC_", "NODE_", "NPM_CONFIG_", "npm_config_", "UV_", "PIP_")
ENV_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)", re.IGNORECASE)


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"  # stdio only; remote transports are rejected
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    disabled: bool = False
    auto_connect: bool = False
    timeout: Optional[float] = None
    keywords: List[str] = field(default_factory=list)
    description: str = ""
    allow_tools: List[str] = field(default_factory=list)
    deny_tools: List[str] = field(default_factory=list)
    confirm_tools: List[str] = field(default_factory=list)
    auto_approve_tools: List[str] = field(default_factory=list)

    # ---- serialisation -------------------------------------------------
    _KEYMAP = {
        "autoConnect": "auto_connect",
        "allowTools": "allow_tools",
        "denyTools": "deny_tools",
        "confirmTools": "confirm_tools",
        "autoApproveTools": "auto_approve_tools",
    }

    def to_json(self) -> Dict[str, Any]:
        """Serialise to the camelCase file format (without the name)."""
        d = asdict(self)
        d.pop("name")
        out: Dict[str, Any] = {}
        reverse = {v: k for k, v in self._KEYMAP.items()}
        for k, v in d.items():
            if v in (None, "", [], {}) and k not in ("command",):
                continue
            if k == "disabled" and not v:
                continue
            if k == "auto_connect" and not v:
                continue
            if k == "transport" and v == "stdio":
                continue
            out[reverse.get(k, k)] = v
        return out

    def to_public(self) -> Dict[str, Any]:
        """Redacted view for APIs / UI: env values hidden."""
        return {
            "name": self.name,
            "transport": self.transport,
            "command": os.path.basename(self.command) if self.command else None,
            "args_count": len(self.args),
            "env_names": sorted(self.env.keys()),
            "cwd": self.cwd,
            "disabled": self.disabled,
            "auto_connect": self.auto_connect,
            "timeout": self.timeout,
            "keywords": list(self.keywords),
            "description": self.description,
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "confirm_tools": list(self.confirm_tools),
            "auto_approve_tools": list(self.auto_approve_tools),
        }


class MCPConfigError(ValueError):
    pass


def _str_list(value: Any, field_name: str, server: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float)) for v in value):
        return [str(v) for v in value]
    raise MCPConfigError(f"{server}: '{field_name}' must be a list of strings")


def _str_dict(value: Any, field_name: str, server: str) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MCPConfigError(f"{server}: '{field_name}' must be an object")
    return {str(k): str(v) for k, v in value.items()}


def parse_server(name: str, raw: Dict[str, Any]) -> MCPServerConfig:
    """Validate one server entry. Raises MCPConfigError on invalid input."""
    if not SERVER_NAME_RE.match(name or ""):
        raise MCPConfigError(
            f"Invalid server name '{name}': use 1-32 letters, digits, '-' or '_'"
        )
    if not isinstance(raw, dict):
        raise MCPConfigError(f"{name}: server entry must be an object")

    raw = {MCPServerConfig._KEYMAP.get(k, k): v for k, v in raw.items()}
    transport = str(raw.get("transport") or raw.get("type") or "stdio").lower()
    if transport != "stdio" or raw.get("url") or raw.get("headers"):
        raise MCPConfigError(
            f"{name}: remote MCP servers are not supported; configure a local "
            f"stdio server with 'command'"
        )

    command_raw = raw.get("command")
    args = _str_list(raw.get("args"), "args", name) if not isinstance(raw.get("args"), dict) else []
    if isinstance(command_raw, (list, tuple)):
        parts = _str_list(command_raw, "command", name)
        command = parts[0] if parts else ""
        args = parts[1:] + args
    else:
        command = str(command_raw or "")

    cfg = MCPServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=args,
        env=_str_dict(raw.get("env"), "env", name),
        cwd=str(raw["cwd"]) if raw.get("cwd") else None,
        disabled=bool(raw.get("disabled", False)),
        auto_connect=bool(raw.get("auto_connect", False)),
        timeout=float(raw["timeout"]) if raw.get("timeout") not in (None, "") else None,
        keywords=_str_list(raw.get("keywords"), "keywords", name),
        description=str(raw.get("description") or "")[:500],
        allow_tools=_str_list(raw.get("allow_tools"), "allowTools", name),
        deny_tools=_str_list(raw.get("deny_tools"), "denyTools", name),
        confirm_tools=_str_list(raw.get("confirm_tools"), "confirmTools", name),
        auto_approve_tools=_str_list(raw.get("auto_approve_tools"), "autoApproveTools", name),
    )

    if not cfg.command:
        raise MCPConfigError(f"{name}: server requires 'command'")
    if cfg.timeout is not None and not (0 < cfg.timeout <= 3600):
        raise MCPConfigError(f"{name}: 'timeout' must be between 0 and 3600 seconds")
    return cfg


def parse_config_document(doc: Any, source: str) -> Tuple[Dict[str, MCPServerConfig], List[str]]:
    """Parse a whole config document. Returns (servers, errors); never raises."""
    servers: Dict[str, MCPServerConfig] = {}
    errors: List[str] = []
    if doc in (None, "", {}):
        return servers, errors
    if not isinstance(doc, dict):
        return servers, [f"{source}: top level must be a JSON object"]
    entries = doc.get("mcpServers", doc) if isinstance(doc.get("mcpServers"), dict) else doc
    for name, raw in entries.items():
        if name == "mcpServers":
            continue
        try:
            servers[name] = parse_server(name, raw)
        except MCPConfigError as e:
            errors.append(f"{source}: {e}")
        except Exception as e:  # defensive: never let one bad entry break loading
            errors.append(f"{source}: {name}: {e}")
    return servers, errors


def load_server_configs(config_file: str, env_json: str = "") -> Tuple[Dict[str, MCPServerConfig], List[str]]:
    """Load servers from env JSON then the config file (file wins on conflict)."""
    servers: Dict[str, MCPServerConfig] = {}
    errors: List[str] = []

    if env_json and env_json.strip() not in ("", "{}"):
        try:
            s, e = parse_config_document(json.loads(env_json), "GUAARDVARK_MCP_SERVERS")
            servers.update(s)
            errors.extend(e)
        except json.JSONDecodeError as e:
            errors.append(f"GUAARDVARK_MCP_SERVERS: invalid JSON ({e.msg})")

    if config_file and os.path.exists(config_file):
        try:
            with open(config_file, encoding="utf-8") as f:
                doc = json.load(f)
            s, e = parse_config_document(doc, os.path.basename(config_file))
            servers.update(s)
            errors.extend(e)
        except json.JSONDecodeError as e:
            errors.append(f"{os.path.basename(config_file)}: invalid JSON ({e.msg} at line {e.lineno})")
        except OSError as e:
            errors.append(f"{os.path.basename(config_file)}: cannot read ({e.strerror})")

    for err in errors:
        logger.error(f"MCP config: {err}")
    return servers, errors


def read_config_file(config_file: str) -> Dict[str, Any]:
    """Return the raw ``mcpServers`` map from the config file (empty if absent)."""
    if not os.path.exists(config_file):
        return {}
    with open(config_file, encoding="utf-8") as f:
        doc = json.load(f)
    if isinstance(doc, dict) and isinstance(doc.get("mcpServers"), dict):
        return dict(doc["mcpServers"])
    return dict(doc) if isinstance(doc, dict) else {}


def write_config_file(config_file: str, servers: Dict[str, Any]) -> None:
    """Atomically write ``{"mcpServers": servers}`` with 0600 permissions."""
    directory = os.path.dirname(config_file) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mcp_servers.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, config_file)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resolve_env_refs(value: str, environ: Optional[Dict[str, str]] = None) -> str:
    """Expand ``${VAR}`` references from the backend environment."""
    environ = os.environ if environ is None else environ
    return _ENV_REF_RE.sub(lambda m: environ.get(m.group(1), ""), value)


def build_child_env(cfg: MCPServerConfig, environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Minimal environment for a stdio server: allowlisted vars + its own env.

    Values in the server's ``env`` block may use ``${VAR}`` to pull a specific
    secret from the backend environment deliberately.
    """
    environ = dict(os.environ if environ is None else environ)
    env: Dict[str, str] = {}
    for key, val in environ.items():
        if key in ENV_PASSTHROUGH_EXACT or key.startswith(ENV_PASSTHROUGH_PREFIXES):
            if ENV_SECRET_RE.search(key) and key not in ENV_PASSTHROUGH_EXACT:
                continue
            env[key] = val
    for key, val in cfg.env.items():
        env[key] = resolve_env_refs(val, environ)
    return env
