"""Install apt packages from a Flask request, without a terminal.

A web handler cannot answer an interactive sudo prompt. Stock Ubuntu does not
grant passwordless sudo, so `sudo apt-get install` from an API route fails (or
hangs until timeout) on the machine a person actually uses. Two ways in, tried
in order:

  1. ``sudo -n`` — silent when the host is configured for it
  2. ``pkexec`` — raises a password dialog on the user's desktop

When neither is possible the caller hands back a command to paste. Package
names are allowlisted at the call site: they are interpolated into a shell
line run as root.
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from typing import Callable, FrozenSet, Iterable, Optional

logger = logging.getLogger(__name__)

SudoProbe = Callable[[], bool]
DesktopProbe = Callable[[], bool]


def passwordless_sudo_available() -> bool:
    """True when ``sudo -n`` runs without prompting for a password."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def desktop_session_available() -> bool:
    """True when pkexec can raise a graphical password prompt.

    pkexec defers to polkit, which needs an authentication agent attached to the
    caller's session. The backend inherits DISPLAY/WAYLAND_DISPLAY and the
    session bus when launched from a desktop session; with none of those there
    is nothing to prompt on.
    """
    if not shutil.which("pkexec"):
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return bool(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("XDG_RUNTIME_DIR")
    )


def manual_apt_command(packages: Iterable[str]) -> str:
    """The exact line a user should paste to install ``packages`` themselves."""
    return "sudo apt-get update && sudo apt-get install -y " + " ".join(packages)


def apt_install_script(packages: list, *, allowed: FrozenSet[str]) -> str:
    """One shell line that refreshes the index then installs ``packages``.

    Combined on purpose: pkexec authenticates per invocation, so splitting
    update and install would ask for a password twice. The index refresh is
    best-effort — a warm cache can still satisfy the install.
    """
    unknown = [p for p in packages if p not in allowed]
    if unknown:
        raise ValueError(f"Refusing to install unexpected packages: {unknown}")
    quoted = " ".join(shlex.quote(p) for p in packages)
    return (
        "apt-get update -qq || true; "
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y {quoted}"
    )


def escalation_method(
    *,
    sudo_probe: Optional[SudoProbe] = None,
    desktop_probe: Optional[DesktopProbe] = None,
) -> str:
    """``sudo``, ``pkexec``, or ``none`` — what a later install would use."""
    if (sudo_probe or passwordless_sudo_available)():
        return "sudo"
    if (desktop_probe or desktop_session_available)():
        return "pkexec"
    return "none"


def run_privileged_apt(
    packages: list,
    *,
    allowed: FrozenSet[str],
    timeout: int = 600,
    log_label: str = "apt",
    sudo_probe: Optional[SudoProbe] = None,
    desktop_probe: Optional[DesktopProbe] = None,
) -> dict:
    """Install apt packages as root, without a terminal.

    Returns method=``none`` when neither escalation route exists, so the
    caller can fall back to telling the user what to run by hand.
    """
    script = apt_install_script(packages, allowed=allowed)
    sudo_ok = (sudo_probe or passwordless_sudo_available)()
    desktop_ok = (desktop_probe or desktop_session_available)()

    if sudo_ok:
        cmd, method = ["sudo", "-n", "/bin/sh", "-c", script], "sudo"
    elif desktop_ok:
        cmd, method = ["pkexec", "/bin/sh", "-c", script], "pkexec"
    else:
        return {"ok": False, "method": "none", "returncode": None, "stderr": ""}

    logger.info("%s: escalating via %s for %s", log_label, method, packages)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "method": method, "returncode": "timeout", "stderr": ""}

    return {
        "ok": result.returncode == 0,
        "method": method,
        "returncode": result.returncode,
        "stderr": (result.stderr or ""),
    }
