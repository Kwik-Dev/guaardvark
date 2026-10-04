"""`guaardvark llm` — the chat provider and the master cloud switch.

The fork's whole reason to exist: upstream removed the cloud chat providers, this fork
kept them. Until now it had no terminal surface at all (CLI_PLAN 3.1).

`cloud on` is the one place in this package that decides whether the user's prompts leave
the machine, so it asks for `--yes` and says what it is about to do. That gate is real,
not decorative: the switch is a DB setting with no generic route — `POST
/api/llm/cloud-enabled` is the only way to write it, and `guaardvark settings set` cannot
reach it (settings is a set of typed routes, and `settings get cloud_models_enabled` is a
404). Turning it *off* needs no confirmation; the safe direction is free.
"""
from __future__ import annotations

import typer

from llx import output
from llx.client import LlxError, get_client

from ._common import fail, json_mode, pick_dict, pick_list, resolve_server, success

COMMAND_NAME = "llm"
app = typer.Typer(help="Chat provider and the master cloud switch.", no_args_is_help=True)

BASE = "/api/llm"


@app.command("provider")
def llm_provider(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Which provider chat uses, and whether the cloud switch is on."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/provider")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_kv(
        {k: payload[k] for k in ("provider", "cloud_models_enabled", "cloud_active") if k in payload},
        title="LLM provider",
    )


@app.command("set")
def llm_set(
    provider: str = typer.Argument(..., help="ollama, mistral or openai"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Switch the active chat provider.

    A cloud provider is refused while the master switch is off or its key is missing —
    the backend decides that, and reports why.
    """
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/provider", json={"provider": provider})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Provider set to {provider}.")


@app.command("models")
def llm_models(
    provider: str = typer.Option(None, "--provider", "-p", help="Defaults to the active one"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Models a provider offers. Ollama lives in `models list` instead."""
    json_out = json_mode(json_out)
    params = {"provider": provider} if provider else {}
    try:
        data = get_client(resolve_server(server)).get(f"{BASE}/provider/models", **params)
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    rows = pick_list(data, "models")
    if json_out or output.is_pipe():
        success({**payload, "models": rows})
        return
    if payload.get("see"):
        output.print_warning(f"Ollama models are listed by `guaardvark models list` ({payload['see']}).")
        return
    output.print_json({**payload, "models": rows})


@app.command("openai-model")
def llm_openai_model(
    model: str = typer.Argument(..., help="Model id the endpoint serves"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Set the active OpenAI-compatible model."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/provider/openai-model", json={"model": model})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"OpenAI model set to {model}.")


@app.command("mistral-model")
def llm_mistral_model(
    model: str = typer.Argument(..., help="e.g. mistral-large-latest"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Set the active Mistral model."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/provider/mistral-model", json={"model": model})
    except LlxError as exc:
        fail(exc)
    if json_out or output.is_pipe():
        success(pick_dict(data))
        return
    output.print_success(f"Mistral model set to {model}.")


@app.command("test")
def llm_test(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Live round-trip against the active cloud provider, to prove key and model work."""
    json_out = json_mode(json_out)
    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/provider/test")
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    output.print_json(payload)


@app.command("cloud")
def llm_cloud(
    state: str = typer.Argument(..., help="on or off"),
    yes: bool = typer.Option(False, "--yes", help="Confirm that prompts will leave this machine"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """The master cloud switch.

    On: chat, and every other call gated on consent, may go to a cloud provider.
    Off: nothing leaves the machine, and no local model is loaded to compensate while a
    cloud endpoint is configured.
    """
    json_out = json_mode(json_out)
    wanted = state.strip().lower()
    if wanted not in ("on", "off"):
        output.print_error(f"Expected 'on' or 'off', got {state!r}.", code="BAD_ARGUMENT")
        raise typer.Exit(2)

    enabling = wanted == "on"
    if enabling and not yes:
        output.print_error(
            "Turning the cloud switch ON sends your prompts to a third-party provider. "
            "Re-run with --yes if that is what you want.",
            code="CONFIRMATION_REQUIRED",
        )
        raise typer.Exit(2)

    try:
        data = get_client(resolve_server(server)).post(f"{BASE}/cloud-enabled", json={"enabled": enabling})
    except LlxError as exc:
        fail(exc)
    payload = pick_dict(data)
    if json_out or output.is_pipe():
        success(payload)
        return
    if enabling:
        provider = payload.get("provider") or "the configured cloud provider"
        output.print_success(f"Cloud models ON — prompts may be sent to {provider}.")
    else:
        output.print_success("Cloud models OFF — nothing leaves this machine.")
