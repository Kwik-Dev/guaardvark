"""System-prompt blocks for the agent loop.

AgentExecutor and the BrainState "agent" and "vision" roles both build the
tail of their system prompt with build_agent_prompt_tail(), so each rule is
written once. The chat tier has its own blocks in chat_prompt_blocks.py.
"""

import json

AGENT_INTRO = (
    "You are an AI assistant with access to tools. Help the user by using tools when needed."
)

JSON_RESPONSE_FORMAT = """RESPONSE FORMAT:
You MUST respond with a JSON object. Every response must have these three fields:
- "thoughts": your reasoning about what to do (string or null)
- "tool_calls": array of tool calls to execute (empty array if none needed)
- "final_answer": your final answer to the user (string or null)

Each tool call object has: "tool_name" (string), "parameters" (object), and optional "reasoning" (string).

EXAMPLE - Using a tool:
{"thoughts": "I need to read the file first", "tool_calls": [{"tool_name": "read_code", "parameters": {"filepath": "config.py"}, "reasoning": "Need to see current config"}], "final_answer": null}

EXAMPLE - Final answer (no tools needed):
{"thoughts": "I have all the information", "tool_calls": [], "final_answer": "The config file sets DEBUG to True on line 1."}"""

# Recipes (data/agent/recipes.json) only run for a short single goal; the
# screen agent skips them for step lists and "go to X and then ..." tasks.
SCREEN_WORKFLOW = """SCREEN:
- You work a virtual screen (display :99) with Firefox on a desktop. It is separate from the person's own screen.
- agent_task_execute starts the screen by itself and carries out one task per call. Give it one short, plain goal such as "open youtube.com" or "search YouTube for cats", not a list of steps; call it again for the next goal.
- To check what is on the screen, call agent_screen_capture with a question about it.
- If the screen cannot do it and you have web_search, use web_search for the answer."""

GROUNDING_RULES = """RULES:
- Use the exact parameter names from the tool descriptions and include every required parameter.
- Only state facts found in tool results. NEVER fabricate information.
- If a tool fails, try a different tool or different parameters. Never repeat the same call.
- When the task is done, give the final answer.
- If you cannot complete the task, say so plainly."""


def user_context_line(context) -> str:
    """A caller's context dict as one line for the agent's first message; "" when empty."""
    if not context:
        return ""
    try:
        return "User context: " + json.dumps(context, default=str)
    except (TypeError, ValueError):
        return "User context: " + str(context)


def build_agent_prompt_tail(
    *,
    tool_schemas: str = "",
    native: bool = False,
    screen: bool = False,
    agent_name: str = "",
    agent_prompt: str = "",
    budget_line: str = "",
) -> str:
    """The agent-loop part of a system prompt, after any persona or memory prefix.

    Args:
        tool_schemas: the tool list in the executor's json_prompt format, for
            the tools this run can call and no others.
        native: the model takes tools through the API's tools parameter, so the
            text carries neither the tool list nor the JSON response format.
        screen: the task works the agent's virtual screen.
        agent_name, agent_prompt: a configured agent's name and instructions.
        budget_line: the step budget, for callers without a BrainState prefix.
    """
    intro = AGENT_INTRO
    if budget_line.strip():
        intro += "\n" + budget_line.strip()
    parts = [intro]
    if agent_prompt.strip():
        heading = f"AGENT: {agent_name.strip()}\n" if agent_name.strip() else ""
        parts.append(heading + agent_prompt.strip())
    if not native:
        if tool_schemas.strip():
            parts.append("Available Tools:\n" + tool_schemas.strip())
        parts.append(JSON_RESPONSE_FORMAT)
    if screen:
        parts.append(SCREEN_WORKFLOW)
    parts.append(GROUNDING_RULES)
    return "\n\n".join(parts)
