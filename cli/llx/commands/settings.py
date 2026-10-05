"""Settings commands — get, set, list.

One upstream-`cli/` edit is deliberate here: `list` used to carry a hand-kept list of
seven keys, which is exactly the inconsistency it had (it omitted keys `get` accepted,
and listed `rag_debug`, which has no route). The list now comes from the backend's
`GET /api/settings`, which is also where `settable` is decided, so the CLI cannot drift.
"""

import typer
from llx.client import get_client, LlxError, LlxConnectionError
from llx.global_opts import get_global_json, get_global_server
from llx import output

settings_app = typer.Typer(help="Application settings", no_args_is_help=True)


def _fetch_settings(client) -> dict:
    """The backend's canonical settings payload: {settings, settable, descriptions}."""
    data = client.get("/api/settings")
    payload = data.get("data", data)
    return payload if isinstance(payload, dict) else {}


@settings_app.command("list")
def settings_list(
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Show all settings."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        payload = _fetch_settings(client)
        settings = payload.get("settings", {})
        settable = payload.get("settable", {})

        if json_out or output.is_pipe():
            output.print_json(
                {"status": "success", "data": {"settings": settings, "settable": settable}}
            )
            return

        # Studio-only keys are marked, so `list` says which keys `set` will accept.
        rows = {}
        for key, val in settings.items():
            label = key if settable.get(key, True) else f"{key} (studio-only)"
            rows[label] = str(val)
        output.print_kv(rows, title="Settings")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@settings_app.command("get")
def settings_get(
    key: str = typer.Argument(..., help="Setting key"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Get a setting value."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        client = get_client(server)
        data = client.get(f"/api/settings/{key}")
        result = data.get("data", data)
        # Typed routes answer with their own single field ({"allow_web_search": ...});
        # unwrap it so `get web_access` prints the value, not a one-key dict.
        if isinstance(result, dict) and len(result) == 1:
            result = next(iter(result.values()))
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {key: result}})
        else:
            output.print_kv({key: str(result)})
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)


@settings_app.command("set")
def settings_set(
    key: str = typer.Argument(..., help="Setting key"),
    value: str = typer.Argument(..., help="Setting value"),
    server: str = typer.Option(None, "--server", "-s"),
    json_out: bool = typer.Option(False, "--json", "-j"),
):
    """Set a setting value."""
    server = server or get_global_server()
    json_out = json_out or get_global_json()
    output.set_json_mode(json_out)
    try:
        parsed: str | bool | int = value
        if value.lower() in ("true", "false"):
            parsed = value.lower() == "true"
        elif value.isdigit():
            parsed = int(value)

        client = get_client(server)

        # Read the canonical list first: it says which keys the CLI may set, and it is
        # the only guard in front of a composite setting (the typed routes accept the
        # Studio's own shape). If it cannot be read, refuse rather than post blindly.
        try:
            settable = _fetch_settings(client).get("settable", {})
        except LlxError as e:
            output.print_error(
                f"Could not read the settings list to check '{key}': {e}", code="API_ERROR"
            )
            raise typer.Exit(1)
        if settable.get(key) is False:
            output.print_error(
                f"'{key}' is a composite setting set in the Studio; the CLI cannot set it.",
                code="STUDIO_ONLY",
            )
            raise typer.Exit(1)

        client.post(f"/api/settings/{key}", json={key: parsed})
        if json_out or output.is_pipe():
            output.print_json({"status": "success", "data": {key: parsed}})
        else:
            output.print_success(f"Set {key} = {parsed}")
    except LlxConnectionError as e:
        output.print_error(str(e), code="CONNECTION_ERROR")
        raise typer.Exit(1)
    except LlxError as e:
        output.print_error(str(e), code="API_ERROR")
        raise typer.Exit(1)
