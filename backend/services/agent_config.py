#!/usr/bin/env python3

import copy
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from backend.config import GUAARDVARK_PROJECT_NAME as _PROJECT_NAME

logger = logging.getLogger(__name__)

AGENT_STATE_FILE = Path(os.environ.get("GUAARDVARK_ROOT", ".")) / "data" / "agent_state.json"

EDITABLE_FIELDS: Tuple[str, ...] = ("enabled", "max_iterations", "system_prompt", "model")
# Reset to default with no fields named puts these back; the on/off switch stays.
RESET_FIELDS: Tuple[str, ...] = ("system_prompt", "max_iterations", "model")
MAX_ITERATIONS_LIMIT = 50
MAX_PROMPT_CHARS = 20000
MAX_MODEL_CHARS = 200

# get_agent_for_message returns None only when General Assistant, the catch-all, is off.
NO_AGENT_MATCHES = "No enabled agent matches this request; General Assistant is off on the Agents page"


class AgentType(Enum):
    CONTENT_CREATOR = "content_creator"
    CODE_ASSISTANT = "code_assistant"
    DATA_ANALYST = "data_analyst"
    RESEARCH_AGENT = "research_agent"
    GENERAL_ASSISTANT = "general_assistant"
    ORCHESTRATOR = "orchestrator"


@dataclass
class AgentConfig:
    id: str
    name: str
    description: str
    agent_type: AgentType
    tools: List[str]
    system_prompt: str
    max_iterations: int = 10
    enabled: bool = True
    priority: int = 0
    trigger_patterns: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Optional model pin. None = the process-wide active LLM (historical
    # behavior). Set to run this agent on a specific local model — e.g. an
    # autoresearch judge must not share the proposer's model.
    model: Optional[str] = None
    # Fields a person may change on the Agents page; saved overrides of any
    # other field are ignored on load.
    editable: Tuple[str, ...] = EDITABLE_FIELDS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "agent_type": self.agent_type.value,
            "tools": self.tools,
            "system_prompt": self.system_prompt,
            "max_iterations": self.max_iterations,
            "enabled": self.enabled,
            "priority": self.priority,
            "trigger_patterns": self.trigger_patterns,
            "metadata": self.metadata,
            "model": self.model
        }


DEFAULT_AGENTS: Dict[str, AgentConfig] = {
    "content_creator": AgentConfig(
        id="content_creator",
        name="Content Creator",
        description="Writes SEO web pages and delivers them as WordPress import rows or a bulk import CSV.",
        agent_type=AgentType.CONTENT_CREATOR,
        tools=[
            "generate_wordpress_content",
            "generate_enhanced_wordpress_content",
            "generate_bulk_csv",
            "get_generation_status",
            "generate_csv",
        ],
        system_prompt="""You write SEO web pages and deliver them in WordPress import format.

- One page as text: generate_wordpress_content. Use generate_enhanced_wordpress_content instead when the page should draw on the person's indexed documents or stick to named services.
- Many pages in one import file: generate_bulk_csv, then get_generation_status with the job_id it returned (as batch_id) until it reports complete.
- Any other table: generate_csv.

Take client, website, topic, industry and audience from the request and its context. This run cannot ask questions: leave a missing detail out and list what was missing in your answer. Report what the tools returned (path, rows, job status); do not describe pages you did not generate.""",
        max_iterations=5,
        enabled=True,
        priority=10,
        trigger_patterns=[
            r"wordpress",
            r"csv.*content",
            r"bulk.*pages?",
            r"generate.*\d+.*(?:pages?|articles?|posts?)",
            r"seo.*content",
        ],
        metadata={"group": "create", "summary": "SEO pages for WordPress", "needs": []},
    ),

    "code_assistant": AgentConfig(
        id="code_assistant",
        name="Code Assistant",
        description="Finds, reads and changes source code in indexed repositories and this project.",
        agent_type=AgentType.CODE_ASSISTANT,
        tools=[
            "search_codebase",
            "search_code",
            "list_code_repositories",
            "get_repository_map",
            "list_code_files",
            "read_code",
            "read_ast_node",
            "edit_code",
            "verify_change",
            "check_inbound_change",
            "codegen",
            "generate_file",
            "analyze_code",
        ],
        system_prompt="""You work on source code.

- Find: search_codebase finds code by meaning, search_code finds exact text, list_code_files lists files. For an overview of a repository, get its folder_id from list_code_repositories, then call get_repository_map.
- Read: read_code reads a file; read_ast_node reads one class or function (it takes the folder_id too).
- Change: edit_code replaces text that occurs exactly once in the file, whitespace included; verify_change confirms the change is there. Run check_inbound_change first on a change that adds network calls, shell commands, dependencies or agent instructions.
- New code: codegen writes code, generate_file saves a new file, analyze_code reviews code.

Read a file before you edit it. If edit_code says "not found" or "multiple occurrences", read the file again and widen old_text until it matches once. Make one change at a time and verify it. edit_code needs a person's approval: where none can be given it is refused, and then you put the exact old and new text in your answer instead.""",
        max_iterations=15,
        enabled=True,
        priority=8,
        trigger_patterns=[
            r"\bcode(?:base)?\b",
            r"\bprogram(?:ming)?\s+(?:in|language|that|to)\b",
            r"\.(?:py|js|jsx|ts|tsx|java|cpp|go|rs)\b",
            r"(?<!\w)(?:python|javascript|typescript|java|rust|golang|bash|shell|sql|c\+\+|react)(?!\w)"
            r".{0,40}\b(?:function|class|method|script|module|error)s?\b",
            r"\b(?:write|create)\s+(?:a|an)\b.{0,40}\b(?:script|function|class)\b",
            r"\bfix\b.{0,40}\bbugs?\b",
            r"\brefactor",
            r"\b(?:edit|modify|change)\b.{0,40}\b(?:file|code)\b",
            r"\bremove\b.{0,40}\bbutton\b",
            r"\badd\b.{0,40}\bfeature\b",
            r"\bupdate\b.{0,40}\bcomponent\b",
            r"\b(?:analy[sz]e|review)\b.{0,40}\bcode\b",
        ],
        metadata={"group": "create", "summary": "Finds, reads and edits code", "needs": []},
    ),

    "data_analyst": AgentConfig(
        id="data_analyst",
        name="Data Analyst",
        description="Reads data files (CSV, Excel, JSON, XML, YAML) and writes new ones.",
        agent_type=AgentType.DATA_ANALYST,
        tools=[
            "process_file",
            "generate_csv",
            "generate_file",
        ],
        system_prompt="""You read and produce data files: CSV, Excel, JSON, XML and YAML.

- process_file reads a file the person names and returns its text; for Excel it returns each sheet's size, column names and first 20 rows.
- generate_csv writes a table; generate_file writes JSON, XML, YAML or any other text file.

Give every table one header row and one type of value per column. Rows you make up are sample data: say so in your answer. Quote figures exactly as process_file returned them, and say when a sheet holds more rows than it returned.""",
        max_iterations=5,
        enabled=True,
        priority=5,
        trigger_patterns=[
            r"\bdata(?:sets?)?\b(?!\s*base)",
            r"\b(?:spreadsheets?|excel|xlsx)\b",
            r"\.(?:csv|json|xml|xlsx)\b",
        ],
        metadata={"group": "create", "summary": "Reads and writes data files", "needs": []},
    ),

    "research_agent": AgentConfig(
        id="research_agent",
        name="Web Research Agent",
        description="Answers questions from the web: searches, reads the best pages and cites them.",
        agent_type=AgentType.RESEARCH_AGENT,
        tools=[
            "web_search",
            "fetch_url",
            "analyze_website",
        ],
        system_prompt="""You answer questions from the web.

- web_search finds pages.
- fetch_url reads one page; pass the person's question as query to get the part of the page that answers it.
- analyze_website audits a page's title, meta description and SEO.

Search, read the two or three most relevant results, answer, and stop. Give the URL behind each claim. Say when sources disagree or do not answer the question. If a tool reports that web access is off, tell the person it is switched on in Settings, and stop.""",
        max_iterations=8,
        enabled=True,
        priority=7,
        trigger_patterns=[
            r"research",
            r"web.*search",
            r"analyze.*website",
            r"website.*analysis",
            r"http://",
            r"https://",
            r"www\.",
            r"\.com\b",
            r"\.org\b",
            r"\.net\b",
            r"what.*online",
            r"find.*information",
            r"search.*web",
        ],
        metadata={"group": "web", "summary": "Searches and reads the web", "needs": ["web_access"]},
    ),

    "browser_automation": AgentConfig(
        id="browser_automation",
        name="Browser Automation Agent",
        description="Drives a browser page by page: opens it, waits, clicks, fills forms and extracts content.",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "browser_navigate",
            "browser_wait",
            "browser_click",
            "browser_fill",
            "browser_extract",
            "browser_get_html",
            "browser_screenshot",
            "browser_execute_js",
            "fetch_url",
            "analyze_website",
            "web_search",
        ],
        system_prompt="""You drive a browser.

- Open a page with browser_navigate, then browser_wait for the element you need.
- browser_click, browser_fill and browser_extract act on CSS selectors; browser_get_html returns the page source to find them.
- browser_screenshot captures the page; browser_execute_js runs a script on it.

Do not enter personal data, pay, post or send anything unless the request asks for exactly that. If a browser tool fails or is blocked, use fetch_url, analyze_website or web_search instead of retrying it.""",
        max_iterations=10,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"^/browser\b",
            r"(?i)screenshot",
            r"(?i)browse\s+to|navigate\s+to",
            r"(?i)scrape|web\s+scrap",
            r"(?i)fill\s+(out|in)\s+.*form",
            r"(?i)click\s+.*(?:button|link|element)",
            r"(?i)automate.*browser|browser\s+automat",
            r"(?i)extract.*from.*(?:page|site|website)",
            r"(?i)open\s+(?:https?://|\w+\.(?:com|org|net|io|biz|dev))",
            r"(?i)get\s+(?:the\s+)?html",
            r"(?i)execute\s+javascript",
            r"(?i)wait\s+for\s+(?:the\s+)?(?:element|page|button)",
        ],
        metadata={
            "group": "web",
            "summary": "Drives a browser by selector",
            "needs": ["browser_automation", "web_access"],
        },
    ),

    "desktop_automation": AgentConfig(
        id="desktop_automation",
        name="Desktop Automation Agent",
        description="Acts on this computer's own desktop: files, applications, clipboard, notifications and the real screen.",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "file_watch",
            "file_bulk_operation",
            "app_launch",
            "app_list",
            "app_focus",
            "gui_click",
            "gui_type",
            "gui_hotkey",
            "gui_screenshot",
            "gui_locate_image",
            "clipboard_get",
            "clipboard_set",
            "notification_send",
        ],
        system_prompt="""You act on this computer's own desktop, the one the person is using.

- file_watch watches a file or folder for changes.
- file_bulk_operation copies, moves or deletes files by pattern, only inside data/, ~/Documents, ~/Downloads and /tmp.
- app_list lists running applications, app_launch starts an allowed application, app_focus brings a window to the front.
- clipboard_get and clipboard_set read and write the clipboard; notification_send shows a desktop notification.
- gui_click, gui_type, gui_hotkey and gui_screenshot work the real screen. Find a target with gui_locate_image rather than guessing coordinates.

These tools are off unless GUAARDVARK_DESKTOP_AUTOMATION=true, and the screen tools (gui_click, gui_type, gui_hotkey, gui_screenshot, gui_locate_image) also need GUAARDVARK_GUI_AUTOMATION=true. If a tool says it is disabled, say so and stop. Delete only files the person named or that match their pattern exactly. Never type passwords or other secrets.""",
        max_iterations=10,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"^/desktop\b",
            r"(?i)watch\s+(?:the\s+|my\s+)?(?:folder|directory|file)",
            r"(?i)(?:copy|move|delete)\s+(?:all\s+)?(?:files|pdfs|images)",
            r"(?i)(?:bulk|batch)\s+(?:copy|move|delete|rename)",
            r"(?i)open\s+(?:the\s+)?(?:app|application|program)\b",
            r"(?i)launch\s+\w+",
            r"(?i)clipboard",
            r"(?i)send\s+(?:a\s+|me\s+(?:a\s+)?)?notification",
            r"(?i)(?:list|show)\s+(?:running\s+)?(?:apps|applications|processes)",
            r"(?i)click\s+(?:at|on)\s+(?:the\s+)?screen",
            r"(?i)type\s+(?:the\s+)?text",
            r"(?i)(?:hotkey|shortcut|press\s+.*key)",
            r"(?i)desktop\s+automat",
            r"(?i)gui\s+automat",
            r"(?i)focus\s+.*window",
        ],
        metadata={
            "group": "computer",
            "summary": "Files, apps and clipboard here",
            "needs": ["desktop_automation"],
        },
    ),

    "media_control": AgentConfig(
        id="media_control",
        name="Media Player Agent",
        description="Plays music from the music folder and controls playback and volume.",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "media_play",
            "media_control",
            "media_volume",
            "media_status",
        ],
        system_prompt="""You control music playback on this computer.

- media_play plays music matching a query: an artist, song, album or genre. For a generic request such as "play some music", use the query "music"; do not invent search terms.
- media_control pauses, resumes, stops, or skips to the next or previous track.
- media_volume sets the volume: 0-100, +10 or -10, mute or unmute.
- media_status says what is playing.

One successful call completes the task: give your answer right after it. If nothing is found, suggest checking the music folder in Settings. If no player is running, say so.""",
        max_iterations=3,
        enabled=True,
        priority=9,
        trigger_patterns=[
            r"(?i)play\s+(?:some\s+|my\s+)?(?:music|song|songs|track|album|playlist)",
            r"(?i)play\s+[\w\s]+(?:songs?|music|album|playlist)",
            r"(?i)(?:pause|stop|resume)\s+(?:the\s+)?(?:music|song|playback|player|audio)",
            r"(?i)(?:next|skip|previous|prev)\s+(?:song|track)",
            r"(?i)what'?s\s+(?:playing|this\s+song)",
            r"(?i)(?:turn|set)\s+(?:the\s+)?volume",
            r"(?i)volume\s+(?:up|down|\d+)",
            r"(?i)(?:mute|unmute|louder|quieter|softer)",
        ],
        metadata={"group": "computer", "summary": "Plays and controls music", "needs": ["media_player"]},
    ),

    "orchestrator_agent": AgentConfig(
        id="orchestrator_agent",
        name="Task Orchestrator",
        description="Plans a multi-step request and hands each step to another enabled agent.",
        agent_type=AgentType.ORCHESTRATOR,
        # Not a registry tool: the OrchestratorService plans and delegates.
        tools=["delegate_task"],
        # The planner writes its own prompt; there is nothing here to edit.
        system_prompt="",
        max_iterations=5,
        enabled=True,
        priority=100,
        trigger_patterns=[
            r"\bplan\s+and\s+execute\b",
            r"\borchestrate\b",
            r"\bcoordinate\b.{0,40}\bagents?\b",
            r"\bbreak\s+(?:this|it)\s+(?:down|into\s+(?:steps|tasks))\b",
        ],
        metadata={"group": "routing", "summary": "Splits a request across agents", "needs": []},
        editable=("enabled",),
    ),

    "general_assistant": AgentConfig(
        id="general_assistant",
        name="General Assistant",
        description="Handles requests no specialist agent covers, from the person's documents, memories or the web.",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "web_search",
            "fetch_url",
            "search_knowledge_base",
            "search_memory",
            "generate_file",
        ],
        system_prompt="""You handle requests that no specialist agent covers.

- For a question about the person's own work, files or earlier conversations, check search_knowledge_base and search_memory before the web.
- web_search finds pages and fetch_url reads one.
- generate_file saves a file the person asks for.

When no tool is needed, answer directly.""",
        max_iterations=10,
        enabled=True,
        priority=0,
        trigger_patterns=[],
        metadata={"group": "routing", "summary": "Takes what no specialist covers", "needs": []},
    ),

    "agent_vision_control": AgentConfig(
        id="agent_vision_control",
        name="Agent Vision Control",
        description="Works the agent's own virtual screen (Firefox on display :99) by sight, separate from the person's screen.",
        agent_type=AgentType.GENERAL_ASSISTANT,
        tools=[
            "agent_mode_start",
            "agent_mode_stop",
            "agent_task_execute",
            "agent_screen_capture",
            "agent_read_text_from_element",
            "agent_status",
        ],
        system_prompt=f"""You operate {_PROJECT_NAME}'s own virtual screen: Firefox on an XFCE desktop on display :99, separate from the person's screen. The person can watch it in the agent screen viewer.

- agent_task_execute carries out one task on the screen, given as a short plain goal.
- agent_screen_capture answers a question about what is on the screen; agent_read_text_from_element reads the exact text in a region of it.
- agent_status reports whether a task is running; agent_mode_stop stops the screen agent when you are done.

On public sites, post, comment or send something only when the request says so in so many words.""",
        max_iterations=10,
        enabled=True,
        priority=15,
        trigger_patterns=[
            r'(?i)virtual\s+(?:display|screen|computer|machine|browser)',
            r'(?i)(?:on|from|using|via|through)\s+(?:the\s+)?virtual',
            r'(?i)agent\s+(?:vision|control|screen|virtual)',
            r'(?i)agent\s+mode',
            r'(?i)what.{0,20}(?:on|see).{0,20}(?:virtual|agent)',
            r'(?i)(?:open|go|navigate|browse|visit|search|click|type|scroll).{0,30}(?:virtual|agent\s+screen)',
            r'(?i)(?:show|tell|describe).{0,20}(?:virtual|agent).{0,10}screen',
            r'(?i)/vision',
            r'(?i)/agent\s',
        ],
        metadata={"group": "computer", "summary": "Works the agent's own screen", "needs": ["screen_agent"]},
    ),
}


class AgentConfigManager:

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}
        self._load_default_agents()
        self._load_saved_state()
        logger.info(f"AgentConfigManager initialized with {len(self._agents)} agents")

    def _load_default_agents(self):
        self._agents = {k: copy.deepcopy(v) for k, v in DEFAULT_AGENTS.items()}

    def _load_saved_state(self):
        """Load persisted agent overrides of each agent's editable fields from disk."""
        try:
            if not AGENT_STATE_FILE.exists():
                return
            with open(AGENT_STATE_FILE, "r") as f:
                saved = json.load(f)
            for agent_id, overrides in saved.items():
                agent = self._agents.get(agent_id)
                if not agent:
                    continue
                for key in agent.editable:
                    if key in overrides:
                        setattr(agent, key, overrides[key])
            logger.info(f"Loaded agent state from {AGENT_STATE_FILE} ({len(saved)} agents)")
        except Exception as e:
            logger.warning(f"Failed to load agent state: {e}")

    def _save_state(self):
        """Persist user-modified agent fields to disk so they survive restarts."""
        try:
            state = {}
            for agent_id, agent in self._agents.items():
                default = DEFAULT_AGENTS.get(agent_id)
                overrides = {}
                if default is None:
                    # Non-default agent — save everything mutable
                    overrides = {
                        "enabled": agent.enabled,
                        "max_iterations": agent.max_iterations,
                        "system_prompt": agent.system_prompt,
                        "model": agent.model,
                    }
                else:
                    # Only save fields that differ from defaults
                    if agent.enabled != default.enabled:
                        overrides["enabled"] = agent.enabled
                    if agent.max_iterations != default.max_iterations:
                        overrides["max_iterations"] = agent.max_iterations
                    if agent.system_prompt != default.system_prompt:
                        overrides["system_prompt"] = agent.system_prompt
                    if agent.model != default.model:
                        overrides["model"] = agent.model
                if overrides:
                    state[agent_id] = overrides
            AGENT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(AGENT_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save agent state: {e}")

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        return self._agents.get(agent_id)

    def list_agents(self) -> List[AgentConfig]:
        agents = list(self._agents.values())
        return sorted(agents, key=lambda a: -a.priority)

    def get_enabled_agents(self) -> List[AgentConfig]:
        return [a for a in self.list_agents() if a.enabled]

    def update_agent(self, agent_id: str, updates: Dict[str, Any]) -> bool:
        """Change an agent's editable fields. False when the agent does not exist.

        Raises ValueError, naming the problem, for a field that is not
        editable on this agent or a value out of range; nothing is changed then.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        for key, value in _validated_updates(agent, updates).items():
            setattr(agent, key, value)

        logger.info(f"Updated agent: {agent_id}")
        self._save_state()
        return True

    def reset_agent(self, agent_id: str, fields: Optional[List[str]] = None) -> bool:
        """Put fields back to the built-in default. False when the agent does not exist.

        With no fields: the instructions, iteration limit and model, never the
        on/off switch. Raises ValueError for a field that is not editable.
        """
        agent = self._agents.get(agent_id)
        default = DEFAULT_AGENTS.get(agent_id)
        if not agent or default is None:
            return False
        if fields is None:
            fields = [key for key in RESET_FIELDS if key in agent.editable]
        bad = [key for key in fields if key not in agent.editable]
        if bad:
            raise ValueError(
                f"Cannot reset {', '.join(bad)} on {agent.name}; editable: {', '.join(agent.editable)}"
            )
        for key in fields:
            setattr(agent, key, copy.deepcopy(getattr(default, key)))
        logger.info(f"Reset agent {agent_id}: {', '.join(fields) or 'nothing'}")
        self._save_state()
        return True

    def describe(self, agent: AgentConfig, registry=None, detail: bool = False) -> Dict[str, Any]:
        """The agent as the Agents page shows it.

        Adds to to_dict(): editable, overridden, group, summary, tools_missing
        (names the registry lacks) and unavailable_reason; with detail, also
        tools_detail [{name, description, requires_approval, installed}].
        """
        data = agent.to_dict()
        meta = agent.metadata or {}
        is_orchestrator = agent.agent_type == AgentType.ORCHESTRATOR
        missing: List[str] = []
        if registry is not None and not is_orchestrator:
            _, missing = registry.subset(agent.tools)
        reason = agent_readiness(agent)
        if not reason and agent.tools and len(missing) == len(agent.tools):
            reason = "None of its tools are available"
        data.update({
            "editable": list(agent.editable),
            "overridden": overridden_fields(agent),
            "group": meta.get("group"),
            "summary": meta.get("summary", ""),
            "tools_missing": missing,
            "unavailable_reason": reason,
        })
        if detail:
            data["tools_detail"] = _tools_detail(agent, registry, is_orchestrator)
        return data

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> bool:
        return self.update_agent(agent_id, {"enabled": enabled})

    def get_agent_for_message(self, message: str) -> Optional[AgentConfig]:
        message_lower = message.lower()

        for agent in self.get_enabled_agents():
            for pattern in agent.trigger_patterns:
                if re.search(pattern, message_lower, re.IGNORECASE):
                    logger.debug(f"Message matched agent '{agent.id}' with pattern: {pattern}")
                    return agent

        general = self._agents.get("general_assistant")
        return general if general is not None and general.enabled else None

    def get_tools_for_agent(self, agent_id: str) -> List[str]:
        agent = self._agents.get(agent_id)
        return agent.tools if agent else []

    def to_dict(self) -> Dict[str, Any]:
        return {
            agent_id: agent.to_dict()
            for agent_id, agent in self._agents.items()
        }


def _validated_updates(agent: AgentConfig, updates: Any) -> Dict[str, Any]:
    """The updates as they will be stored; ValueError for anything not allowed."""
    if not isinstance(updates, dict):
        raise ValueError("Send a JSON object of the fields to change")
    editable = ", ".join(agent.editable)
    clean: Dict[str, Any] = {}
    for key, value in updates.items():
        if key not in agent.editable:
            raise ValueError(f"'{key}' cannot be changed on {agent.name}; editable: {editable}")
        if key == "enabled":
            if not isinstance(value, bool):
                raise ValueError("enabled must be true or false")
        elif key == "max_iterations":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ITERATIONS_LIMIT:
                raise ValueError(f"max_iterations must be a whole number from 1 to {MAX_ITERATIONS_LIMIT}")
        elif key == "system_prompt":
            if not isinstance(value, str):
                raise ValueError("system_prompt must be text")
            if not value.strip():
                raise ValueError("Instructions cannot be empty; use Reset to default to restore the built-in text")
            if len(value) > MAX_PROMPT_CHARS:
                raise ValueError(f"Instructions are limited to {MAX_PROMPT_CHARS:,} characters")
        elif key == "model":
            if value is None or (isinstance(value, str) and not value.strip()):
                value = None
            elif not isinstance(value, str) or len(value.strip()) > MAX_MODEL_CHARS:
                raise ValueError(f"model must be a model name of at most {MAX_MODEL_CHARS} characters, or empty")
            else:
                value = value.strip()
        clean[key] = value
    return clean


def _short_description(text: str, limit: int = 200) -> str:
    """The first sentence of a tool description, for a one-line listing."""
    text = " ".join((text or "").split())
    match = re.search(r"(?<=[.!?])\s", text)
    first = text[:match.start()] if match else text
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _tools_detail(agent: AgentConfig, registry, is_orchestrator: bool) -> List[Dict[str, Any]]:
    if is_orchestrator:
        return [{
            "name": name,
            "description": "Hands each planned step to another enabled agent.",
            "requires_approval": False,
            "installed": True,
        } for name in agent.tools]
    details = []
    for name in agent.tools:
        tool = registry.get_tool(name) if registry is not None else None
        details.append({
            "name": name,
            "description": _short_description(getattr(tool, "description", "")) if tool else "",
            "requires_approval": bool(getattr(tool, "requires_approval", False)),
            "installed": tool is not None,
        })
    return details


def overridden_fields(agent: AgentConfig) -> List[str]:
    """The editable fields where this agent differs from its built-in default."""
    default = DEFAULT_AGENTS.get(agent.id)
    if default is None:
        return []
    return [key for key in agent.editable if getattr(agent, key) != getattr(default, key)]


def _need_unmet(need: str) -> Optional[str]:
    """Why one of an agent's needs is not met on this install, or None when it is."""
    from backend import config as _config
    if need == "web_access":
        from backend.utils.settings_utils import get_web_access
        return None if get_web_access() else "Web access is off in Settings"
    if need == "browser_automation":
        return None if _config.BROWSER_AUTOMATION_ENABLED else "Browser automation is off (GUAARDVARK_BROWSER_AUTOMATION)"
    if need == "desktop_automation":
        return None if _config.DESKTOP_AUTOMATION_ENABLED else "Off until GUAARDVARK_DESKTOP_AUTOMATION=true"
    if need == "screen_agent":
        from backend.utils.platform import screen_agent_available
        return None if screen_agent_available() else "The agent screen needs Linux"
    if need == "media_player":
        from backend.utils.platform import media_player_available
        return None if media_player_available() else "Media playback needs Linux"
    return None


def agent_readiness(agent: AgentConfig) -> Optional[str]:
    """Why this agent cannot do its work on this install, or None when it can.

    Reads the agent's metadata["needs"]; the first unmet need is the reason.
    """
    for need in (agent.metadata or {}).get("needs") or []:
        try:
            reason = _need_unmet(need)
        except Exception as e:
            logger.debug(f"Readiness check '{need}' failed for {agent.id}: {e}")
            reason = None
        if reason:
            return reason
    return None


_config_manager: Optional[AgentConfigManager] = None


def get_agent_config_manager() -> AgentConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = AgentConfigManager()
    return _config_manager


def get_agent(agent_id: str) -> Optional[AgentConfig]:
    return get_agent_config_manager().get_agent(agent_id)


def list_agents() -> List[AgentConfig]:
    return get_agent_config_manager().list_agents()
