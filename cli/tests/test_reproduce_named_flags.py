"""Named `reproduce` lines for images/videos, now that the create commands carry the
recorded scalars (issue #8, task 4).

Before this, `images generate` / `videos generate` exposed a subset of the backend's
fields, so every image/video record fell back to the generic `api request` line. These
tests pin the new flags both ways: the create command sends them, and `reproduce` prefers
the named line when the record is expressible.
"""
from __future__ import annotations

import json

from llx.main import app


def test_images_generate_sends_the_new_flags(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/batch-image/generate/prompts", json={"batch_id": "B1"})

    result = cli_runner.invoke(app, [
        "images", "generate", "a red fox", "--style", "cinematic", "--width", "1024",
        "--height", "768", "--steps", "20", "--guidance", "4.5",
        "--negative-prompt", "blurry", "--no-auto-enhance", "--json"])

    assert result.exit_code == 0, result.output
    call = fake_backend.calls_for("POST", "/api/batch-image/generate/prompts")[0]
    body = json.loads(call[2])
    assert body["prompts"] == ["a red fox"]
    assert body["style"] == "cinematic" and body["width"] == 1024 and body["height"] == 768
    assert body["steps"] == 20 and body["guidance"] == 4.5
    assert body["negative_prompt"] == "blurry" and body["auto_enhance"] is False


def test_images_generate_dry_run_shows_flags_and_sends_nothing(fake_backend, cli_runner, isolated_home):
    fake_backend.default(status=200, json={})

    result = cli_runner.invoke(app, [
        "images", "generate", "a red fox", "--style", "cinematic", "--steps", "20",
        "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["body"]["style"] == "cinematic" and payload["body"]["steps"] == 20
    assert payload["settings"]["style"]["source"] == "explicit"
    assert fake_backend.posted_paths() == []


def test_images_reproduce_prefers_the_named_command(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B1", json={"data": {
        "batch_id": "B1", "retry_data": {"mode": "text", "prompts": ["a red fox"],
                                         "params": {"model": "zimage-turbo", "style": "cinematic",
                                                    "width": 1024, "steps": 20, "auto_enhance": False}}}})

    result = cli_runner.invoke(app, ["images", "reproduce", "B1", "--json"])

    payload = json.loads(result.output)
    assert payload["inexpressible"] == []
    assert payload["named_command_line"].startswith("guaardvark images generate 'a red fox'")
    assert "--style cinematic" in payload["named_command_line"]
    assert "--no-auto-enhance" in payload["named_command_line"]
    assert payload["command_line"] == payload["named_command_line"]
    assert fake_backend.posted_paths() == []


def test_images_reproduce_falls_back_for_a_multi_prompt_batch(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-image/status/B2", json={"data": {
        "batch_id": "B2", "retry_data": {"mode": "text", "prompts": ["one", "two"],
                                         "params": {"model": "m"}}}})

    result = cli_runner.invoke(app, ["images", "reproduce", "B2", "--json"])

    payload = json.loads(result.output)
    assert payload["named_command_line"] is None
    assert "prompts" in payload["inexpressible"]
    assert payload["body"]["prompts"] == ["one", "two"]
    assert payload["command_line"].startswith("guaardvark api request")


def test_videos_generate_sends_negative_prompt_and_prompt_style(fake_backend, cli_runner, isolated_home):
    fake_backend.route("POST", "/api/batch-video/generate/text", json={"batch_id": "V1"})

    result = cli_runner.invoke(app, [
        "videos", "generate", "a kite", "--negative-prompt", "blurry", "--prompt-style", "cinematic",
        "--json"])

    assert result.exit_code == 0, result.output
    call = fake_backend.calls_for("POST", "/api/batch-video/generate/text")[0]
    body = json.loads(call[2])
    assert body["negative_prompt"] == "blurry" and body["prompt_style"] == "cinematic"
    assert body["prompts"] == ["a kite"]


def test_videos_reproduce_prefers_the_named_command(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-video/status/V1", json={
        "batch_id": "V1", "status": "completed", "results": [],
        "retry_data": {"mode": "text", "prompts": ["a kite"],
                       "params": {"model": "wan22-5b", "width": 832, "num_inference_steps": 24,
                                  "guidance_scale": 6.0, "motion_strength": 1.0,
                                  "generate_frames_only": False, "negative_prompt": "blurry",
                                  "prompt_style": "cinematic"}}})

    result = cli_runner.invoke(app, ["videos", "reproduce", "V1", "--json"])

    payload = json.loads(result.output)
    assert payload["inexpressible"] == []
    assert payload["named_command_line"].startswith("guaardvark videos generate 'a kite'")
    assert "--negative-prompt blurry" in payload["named_command_line"]
    assert "--prompt-style cinematic" in payload["named_command_line"]
    assert fake_backend.posted_paths() == []


def test_videos_reproduce_falls_back_when_ui_config_is_present(fake_backend, cli_runner, isolated_home):
    fake_backend.route("GET", "/api/batch-video/status/V2", json={
        "batch_id": "V2", "status": "completed", "results": [],
        "retry_data": {"mode": "text", "prompts": ["a kite"],
                       "params": {"model": "wan22-5b", "ui_config": {"tier": "high"}}}})

    result = cli_runner.invoke(app, ["videos", "reproduce", "V2", "--json"])

    payload = json.loads(result.output)
    assert payload["named_command_line"] is None
    assert "ui_config" in payload["inexpressible"]
    assert payload["body"]["ui_config"] == {"tier": "high"}
    assert payload["command_line"].startswith("guaardvark api request")
