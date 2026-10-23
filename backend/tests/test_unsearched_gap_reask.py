"""Replies that defer without searching the documents get one re-ask."""

import os

os.environ.setdefault("GUAARDVARK_MODE", "test")

import pytest

from backend.services import unified_chat_engine as uce


@pytest.mark.parametrize("reply", [
    "The provided documents do not contain information about how the `/claude chat` command works.",
    "The excerpt itself does not specify what rules a recipe's trigger must follow.",
    "There is no specific port mentioned for the Swarm service itself in these excerpts.",
    "I do not have access to the specific template you are referring to.",
    "I do not have specific knowledge regarding the indexing software you are referring to.",
    "I need more information to tell you how to restrict which extension folders load.",
    "Please provide the security report you are referring to.",
    "The document `INSTALL.md` says the Swarm runs on `127.0.0.1`, but it does not explicitly state the port.",
])
def test_deferring_replies_are_caught(reply):
    assert uce._UNSEARCHED_GAP_RE.search(reply)


@pytest.mark.parametrize("reply", [
    "Ctrl+Shift+C opens or closes the floating chat.",
    "The documents don't need re-indexing after an upgrade.",
    "Guaardvark does not send files off the machine.",
    "Set GUAARDVARK_EXTENSIONS=a,b to load only those two extensions.",
])
def test_answers_are_left_alone(reply):
    assert not uce._UNSEARCHED_GAP_RE.search(reply)
