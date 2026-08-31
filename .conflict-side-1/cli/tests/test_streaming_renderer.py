from llx.streaming import ChatRenderer


def test_tool_call_renderer_displays_params_payload():
    renderer = ChatRenderer()

    renderer.on_tool_call({
        "tool": "edit_code",
        "params": {"filepath": "example.py", "dry_run": True},
    })

    assert "edit_code" in renderer._tool_lines[0]
    assert "dry_run" in renderer._tool_lines[0]
    assert "example.py" in renderer._tool_lines[0]


def test_approval_prompt_rejects_mismatched_edit_target(tmp_path):
    renderer = ChatRenderer()
    expected = tmp_path / "docs" / "test-container.sh"
    expected.parent.mkdir()
    expected.write_text("#!/usr/bin/env bash\n")

    approved = renderer.prompt_for_approval(
        {
            "tools": ["edit_code"],
            "tool_details": [
                {"tool": "edit_code", "params": {"filepath": "CONTRIBUTING.md"}},
            ],
        },
        expected_target=str(expected),
    )

    assert approved is False
