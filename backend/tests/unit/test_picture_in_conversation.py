"""A picture pasted mid-conversation is read against the conversation.

It went to the screen-control prompt, and a vision model described a render of
the prompt it had just written instead of saying why the render missed.
"""
import pytest

from backend.services.agent_brain import vision_prompt_key
from backend.services.chat_prompt_blocks import ATTACHED_PICTURE_IN_CONVERSATION
from backend.services.unified_chat_engine import attached_picture_note

_HISTORY = [
    {"role": "user", "content": "Rewrite this so the camera is directly behind him"},
    {"role": "assistant", "content": "Over-the-shoulder shot from behind ..."},
]


@pytest.mark.parametrize("message", [
    "That did not work, here is how it came out.",
    "what breed is this?",
    "Describe this image.",
])
def test_a_pasted_picture_gets_the_chat_prompt(message):
    assert vision_prompt_key(message, "b64") == "chat"


@pytest.mark.parametrize("message, image", [
    ("what is on the screen right now?", "b64"),
    ("/vision is this the settings page?", "b64"),
    ("click the firefox icon on the screen", None),
])
def test_a_screen_task_keeps_the_screen_prompt(message, image):
    assert vision_prompt_key(message, image) == "vision"


def test_a_picture_after_a_reply_is_put_in_context():
    note = attached_picture_note("That did not work, here is how it came out.", _HISTORY, True)
    assert note == ATTACHED_PICTURE_IN_CONVERSATION


@pytest.mark.parametrize("message, history, has_picture", [
    ("what breed is this?", [], True),
    ("That did not work, here is how it came out.", [{"role": "user", "content": "hi"}], True),
    ("Describe this image.", _HISTORY, True),
    ("", _HISTORY, True),
    ("That did not work.", _HISTORY, False),
])
def test_no_note_without_a_conversation_or_a_picture(message, history, has_picture):
    assert attached_picture_note(message, history, has_picture) is None
