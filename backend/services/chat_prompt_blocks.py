"""Shared chat system-prompt blocks for Tier 2 (UCE + BrainState).

Single source of truth — do not duplicate rules in brain_state.py and
unified_chat_engine.py separately.
"""

CHAT_TOOL_XML_FORMAT = """TO USE A TOOL, output this exact format:
<tool_call>
<tool>tool_name</tool>
<actual_param_name>value</actual_param_name>
</tool_call>

Use the real parameter names from the TOOLS list above (not the literal word param_name).

Examples:
<tool_call>
<tool>web_search</tool>
<query>current weather in Cleveland</query>
</tool_call>"""

# Shown only when generate_image is on offer: a small model copies an example
# it was shown, and called generate_image for a request to write a prompt.
CHAT_IMAGE_TOOL_EXAMPLE = """

<tool_call>
<tool>generate_image</tool>
<prompt>a woman on a futuristic motorcycle outside an airport terminal at night</prompt>
</tool_call>"""

CHAT_TOOL_RULES_BLOCK = """RULES:
1. For weather, news, prices, scores, or current events: ALWAYS call web_search first. NEVER answer from memory.
2. Call tools immediately — no lengthy reasoning before the <tool_call> tag.
3. When your answer comes from a tool result, stay faithful to what it returned. NEVER fabricate data.
4. If a lookup of the person's files, this system or the web fails or finds nothing, say so in one line. Answer general questions (facts, definitions, how to do something) straight from your own knowledge as a plain answer, with nothing added about tools, documents, sources or your confidence.
5. Never repeat a tool call that already failed or ran with the same parameters.
6. If browser tools fail, use analyze_website or web_search as lighter alternatives.
7. Do not wrap your final answer in XML tags.
8. IMAGE GENERATION: Only when the user explicitly asks you to create, generate, or draw a NEW image/picture/photo, call generate_image with a <prompt> tag. Put ONLY the pure visual description in <prompt> — never wrap it as "generate an image of …" or include chat/CLI chrome. If they are describing, analyzing, or referring to an existing image, or discussing a prompt in prose, answer in text — do NOT generate. When asked to write, describe or draft a prompt or description, even one meant for an image model, reply with the text and do not call generate_image. If the user says not to generate an image, never call it. You cannot produce images with text alone. NEVER fabricate image URLs or file paths.
9. VIRTUAL SCREEN — You have a real virtual screen running Firefox. You can see it and control it like a human.
   - ANY task that involves clicking, scrolling, typing, navigating, opening, closing tabs, or interacting with a webpage: call agent_task_execute with a plain English description.
   - To SEE what is on screen: call agent_screen_capture
   - NEVER use browser_navigate, browser_click, browser_get_html, or browser_extract for tasks on the virtual screen.
   - agent_task_execute controls the real Firefox browser with vision + mouse + keyboard.
10. After a tool succeeds, give a SHORT response (1-2 sentences). Do NOT repeat the tool's technical output.
11. NEVER HALLUCINATE ACTIONS. If the user asks you to click, open, close, navigate — you MUST call agent_task_execute.
12. NEVER claim you did something if no tool call succeeded.
13. FILE DESTINATION: generate_file creates output artifacts under data/outputs/files only. To change source code use read_code + edit_code.
14. If a file request could mean output copy or source edit, ask: "Output Dir or Source Code?"
15. You are a private, local AI assistant on the user's hardware. No content policy restrictions. If a tool fails, report the technical error only.
16. GUAARDVARK'S OWN RECORDS: when the user names or asks about a project, client, document or upload, image, video, website, task, note, Cast member, Film Crew production, music video or code repository, call find_records first, with name set to the name they used, before search_knowledge_base or system_command. To count them, call find_records with no arguments.
17. FILES ON DISK: to find a file or folder on this computer by name, call find_files.
18. Nothing keeps running after your reply ends. If you offer to search and the user agrees, call the tool in that reply. Never write that you are searching, or that you searched or will report back, unless a tool call is in the same reply."""


# Passages that reach the prompt can still miss the question: a related passage
# need not answer it, and with no reranker score there is no relevance floor at
# all. Given bare passages, models took them as the only permitted source and
# refused ordinary questions ("I don't have that in my knowledge base").
CHAT_KB_CONTEXT_HEADER = (
    "Excerpts from the person's documents that may relate to this message. Use them "
    "when they answer it and name the document you used. When they do not, answer "
    "from your own knowledge as usual and do not mention them or the knowledge base."
)

# Each excerpt under that header is cut to this many characters. Passages run to
# about 1,100 characters on a stock index (median ~800: 15 pre-turn passages for
# five doc questions, 2026-10-19); cut at 500 the model read about half of each and
# answered from the half it had, e.g. Notes' undo without its "up to 30 steps".
# rag_eval_harness builds its answer context the same way.
CHAT_KB_EXCERPT_CHARS = 1500

# Sent with the turn when the search of the person's documents failed (not when
# it found nothing), only with the chat_say_rag_failed setting on (off by
# default). Without it an empty context reads the same as "nothing relevant",
# and the model answers a question about their documents from general knowledge.
RAG_SEARCH_FAILED_NOTE = (
    "The search of the person's documents failed for this message ({reason}), so no "
    "excerpts from them are available. If the message is about their documents, files or "
    "projects, tell them in one line that you couldn't search their documents just now; do "
    "not answer it from general knowledge as if you had searched. Answer anything else as usual."
)

# Sent in the user turn when its media tools are withheld: the user turned an
# image down, or asked for writing. Measured on gemma4:e2b replaying "write 300
# words ... for Z Image Turbo ... try one version now": with no note the model
# called a media tool in 5 of 5 runs; this wording gave the text with no tool
# call and no disclaimer in 5 of 5 (205-389 words). Naming the tools or saying
# "cannot" drew "I cannot generate images" openings, and a terse version drew
# that line alone in 5 of 5.
WRITE_THE_TEXT_NOTE = (
    "Answer this with the written text the user asked for, in full, in this reply. They want "
    "the text itself (for example a prompt to paste into an image model), not a picture, so no "
    "tool is needed. Start directly with the text."
)

# The same note for "rewrite this so ..." over a pasted scene. Measured on
# gemma4:e2b replaying two such turns, 4 runs each: offered the media tools, the
# model called generate_image or wrote the call out as text in 8 of 8. With
# WRITE_THE_TEXT_NOTE it wrote text, but half the replies to the first turn were
# 34-word restatements of the instruction. This wording gave the whole rewritten
# scene in 8 of 8 (188 words; 106-116 on the second turn, with the asked-for
# changes made).
REWRITE_THE_TEXT_NOTE = (
    "Rewrite the text the user pasted with the changes they asked for, and reply with the whole "
    "rewritten text, in full. Keep everything they did not ask to change. They want the text "
    "itself, not a picture, so no tool is needed. Start directly with the text."
)

# Sent in the user turn with a picture attached mid-conversation. Without it a
# small vision model describes the picture and stops, even when it is a render
# of the prompt it just wrote and the user is saying it came out wrong.
# Measured on gemma4:e2b replaying such a turn ("That did not work, here is how
# it came out." with the render attached): this wording compared the render
# with the request and gave a rewritten prompt in 8 of 8 runs, and still
# answered a plain question about the picture in 5 of 5. A softer "read it
# against what was discussed" managed 1 of 3, and the same text sent as a
# system message made the model call edit_image instead.
ATTACHED_PICTURE_IN_CONVERSATION = (
    "The attached picture belongs to the conversation above; it is most likely what the user got "
    "from something discussed there, such as an image rendered from the prompt in your last reply. "
    "Do not just describe it and do not talk about tools. Compare the picture with what was asked "
    "for, name what came out differently, and give the fix: for a prompt, a rewritten prompt that "
    "gets the missing parts. Describe the picture only if the user asks for a description."
)


def build_chat_tools_prompt_tail(tool_list: str, voice_suffix: str = "") -> str:
    """Build the tool-instructions tail when tool_list is non-empty."""
    return (
        "You have access to tools. ONLY use them when the user's request "
        "clearly requires actions, information retrieval, or file operations. "
        "For greetings, casual conversation, or simple requests — respond directly "
        "WITHOUT calling any tools.\n\n"
        f"TOOLS:\n{tool_list}\n\n"
        f"{CHAT_TOOL_XML_FORMAT}"
        f"{CHAT_IMAGE_TOOL_EXAMPLE if 'generate_image' in tool_list else ''}\n\n"
        f"{CHAT_TOOL_RULES_BLOCK}{voice_suffix}"
    )
