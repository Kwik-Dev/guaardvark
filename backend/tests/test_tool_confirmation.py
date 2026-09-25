"""trusted_caller: the mark that lets approval-gated tools run."""

import threading

from backend.services.tool_confirmation import (
    approval_required_message,
    current_trusted_caller,
    trusted_caller,
)


def test_unset_by_default():
    assert current_trusted_caller() is None


def test_nested_and_restored():
    with trusted_caller("outer"):
        assert current_trusted_caller() == "outer"
        with trusted_caller("inner"):
            assert current_trusted_caller() == "inner"
        assert current_trusted_caller() == "outer"
    assert current_trusted_caller() is None


def test_restored_after_exception():
    try:
        with trusted_caller("x"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert current_trusted_caller() is None


def test_does_not_leak_to_other_threads():
    seen = []
    with trusted_caller("chat_approval"):
        t = threading.Thread(target=lambda: seen.append(current_trusted_caller()))
        t.start()
        t.join()
    assert seen == [None]


def test_message_names_the_tool():
    assert "mcp__fs__write_file" in approval_required_message("mcp__fs__write_file")
