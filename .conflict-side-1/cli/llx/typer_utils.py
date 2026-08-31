"""Shared helpers for invoking Typer-wrapped commands outside the Click CLI."""

from __future__ import annotations

import inspect


def is_required_typer_param(param: inspect.Parameter) -> bool:
    """True when a Typer-wrapped function param must be supplied by the caller."""
    if param.default is inspect.Parameter.empty:
        return True
    cls_name = param.default.__class__.__name__
    if cls_name == "ArgumentInfo":
        default = param.default.default
        return default is inspect.Parameter.empty or default is ...
    return False


def is_typer_wrapped_param(param: inspect.Parameter) -> bool:
    if param.default is inspect.Parameter.empty:
        return False
    return param.default.__class__.__name__ in ("OptionInfo", "ArgumentInfo")


def resolve_typer_default(param: inspect.Parameter):
    """Return the real default for a Typer-wrapped param, or None if required."""
    if param.default is inspect.Parameter.empty:
        return None
    cls_name = param.default.__class__.__name__
    if cls_name in ("OptionInfo", "ArgumentInfo"):
        default = param.default.default
        if default is inspect.Parameter.empty or default is ...:
            return None
        return default
    return param.default


def build_typer_kwargs(sig: inspect.Signature, args: list[str], injected: dict) -> dict | None:
    """Build kwargs for a direct Typer command call from slash args."""
    kwargs = dict(injected)
    required_positional = [
        p
        for p in sig.parameters.values()
        if is_required_typer_param(p) and p.name not in kwargs
    ]

    if required_positional:
        if not args:
            return None
        kwargs[required_positional[0].name] = " ".join(args)

    for param in sig.parameters.values():
        if param.name in kwargs:
            continue
        if is_typer_wrapped_param(param):
            # Inject resolved defaults including explicit None — never leave OptionInfo objects.
            kwargs[param.name] = resolve_typer_default(param)
        elif param.default is not inspect.Parameter.empty:
            kwargs[param.name] = param.default

    return kwargs


def format_command_usage(name: str, func) -> str:
    """Build a one-line usage hint for a simple Typer-backed slash command."""
    sig = inspect.signature(func)
    required = [
        p.name
        for p in sig.parameters.values()
        if is_required_typer_param(p) and p.name not in ("server", "json_out")
    ]
    if required:
        placeholders = " ".join(f"<{n}>" for n in required)
        return f"Usage: /{name} {placeholders}"
    return f"Usage: /{name}"
