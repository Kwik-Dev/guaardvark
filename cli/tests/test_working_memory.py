from llx.utils import parse_file_mentions_with_metadata
from llx.working_memory import (
    apply_attachments,
    apply_user_intent,
    approval_target_mismatch,
    build_cli_context,
    empty_working_memory,
    expected_edit_target,
    record_recommendation_summary,
    should_demote_rag,
)


def test_quoted_absolute_file_pins_active_file(tmp_path):
    target = tmp_path / "Containerfile.test"
    target.write_text("FROM ubuntu:24.04\n")
    _message, attachments = parse_file_mentions_with_metadata(f"'{target}' Summarize this file")
    memory = empty_working_memory()

    apply_attachments(memory, attachments)

    assert memory["active_file"] == str(target)
    assert memory["recent_files"] == [str(target)]


def test_later_explicit_file_switches_active_file(tmp_path):
    first = tmp_path / "Containerfile.test"
    second = tmp_path / "test-container.sh"
    first.write_text("FROM ubuntu:24.04\n")
    second.write_text("#!/usr/bin/env bash\n")
    memory = empty_working_memory()

    _message, first_attachments = parse_file_mentions_with_metadata(f"'{first}' summarize")
    apply_attachments(memory, first_attachments)
    _message, second_attachments = parse_file_mentions_with_metadata(f"'{second}' suggest improvements")
    apply_attachments(memory, second_attachments)

    assert memory["active_file"] == str(second)
    assert memory["recent_files"][0] == str(second)
    assert str(first) in memory["recent_files"]


def test_recommendation_and_implementation_followups_use_active_file(tmp_path):
    target = tmp_path / "test-container.sh"
    target.write_text("#!/usr/bin/env bash\n")
    _message, attachments = parse_file_mentions_with_metadata(f"'{target}' suggest improvements to this file")
    memory = empty_working_memory()

    apply_attachments(memory, attachments)
    apply_user_intent(memory, "suggest improvements to this file")
    apply_user_intent(memory, "Implement those recommended improvements to the file")

    assert memory["last_recommendation"]["file"] == str(target)
    assert expected_edit_target(memory) == str(target)


def test_recommendation_summary_stays_session_scoped(tmp_path):
    target = tmp_path / "test-container.sh"
    target.write_text("#!/usr/bin/env bash\n")
    memory = empty_working_memory()
    memory["active_file"] = str(target)

    record_recommendation_summary(
        memory,
        "suggest improvements to this file",
        "1. Add set -euo pipefail.\n2. Quote variables.",
    )

    assert memory["last_recommendation"]["file"] == str(target)
    assert "set -euo pipefail" in memory["last_recommendation"]["summary"]


def test_cli_context_and_rag_demote_for_deictic_file_followup(tmp_path):
    target = tmp_path / "test-container.sh"
    target.write_text("#!/usr/bin/env bash\n")
    memory = empty_working_memory()
    memory["active_file"] = str(target)
    memory["recent_files"] = [str(target)]

    context = build_cli_context("[System Context]\nServer: ok", memory)

    assert "Active file:" in context
    assert str(target) in context
    assert should_demote_rag("Suggest improvements to the file", memory) is True


def test_approval_guard_rejects_unrelated_edit_target(tmp_path):
    expected = tmp_path / "docs" / "test-container.sh"
    expected.parent.mkdir()
    expected.write_text("#!/usr/bin/env bash\n")
    data = {
        "tools": ["edit_code"],
        "tool_details": [
            {"tool": "edit_code", "params": {"filepath": "CONTRIBUTING.md"}},
        ],
    }

    mismatch, targets = approval_target_mismatch(data, str(expected))

    assert mismatch is True
    assert targets == ["CONTRIBUTING.md"]


def test_approval_guard_accepts_relative_path_under_active_file(tmp_path):
    expected = tmp_path / "docs" / "test-container.sh"
    expected.parent.mkdir()
    expected.write_text("#!/usr/bin/env bash\n")
    data = {
        "tools": ["edit_code"],
        "tool_details": [
            {"tool": "edit_code", "params": {"filepath": "docs/test-container.sh"}},
        ],
    }

    mismatch, _targets = approval_target_mismatch(data, str(expected))

    assert mismatch is False
