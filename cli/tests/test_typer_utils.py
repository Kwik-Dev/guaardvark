"""Tests for shared Typer kwargs resolution."""

import inspect

import typer

from llx.typer_utils import build_typer_kwargs, format_command_usage, resolve_typer_default


def _sample_cmd(
    query: str = typer.Argument(...),
    limit: int = typer.Option(5, "--limit", "-n"),
    server: str = typer.Option(None, "--server", "-s"),
):
    pass


def test_build_kwargs_injects_none_for_optional_options():
    sig = inspect.signature(_sample_cmd)
    kwargs = build_typer_kwargs(sig, ["hello"], {"server": "http://localhost:5000"})
    assert kwargs is not None
    assert kwargs["query"] == "hello"
    assert kwargs["limit"] == 5
    assert kwargs["server"] == "http://localhost:5000"


def test_build_kwargs_returns_none_when_required_missing():
    sig = inspect.signature(_sample_cmd)
    assert build_typer_kwargs(sig, [], {}) is None


def test_format_command_usage():
    assert format_command_usage("search", _sample_cmd) == "Usage: /search <query>"


def test_doctor_path_default_is_none_not_optioninfo():
    from llx.commands.system import doctor

    sig = inspect.signature(doctor)
    path_param = sig.parameters["path"]
    assert resolve_typer_default(path_param) is None
    kwargs = build_typer_kwargs(sig, [], {})
    assert kwargs is not None
    assert kwargs["path"] is None
    assert kwargs["repair"] is False
    assert kwargs["cli_check"] is False
