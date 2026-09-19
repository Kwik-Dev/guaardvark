"""The ComfyUI launch env is rebuilt from .env at every plugin start.

plugins/comfyui/scripts/start.sh used to read GUAARDVARK_COMFYUI_RESERVE_VRAM
and _ATTENTION from the backend process's environment, frozen at backend
start: editing .env and POST /api/plugins/comfyui/restart still launched the
old value. The keys now come from the checkout's .env on top of the inherited
environment, through dotenv_launch_overrides / shell_exports.
"""

import os
import subprocess
import sys
from pathlib import Path

from backend.services.comfyui_launch_flags import (
    LAUNCH_ENV_PREFIX,
    RESERVE_VRAM_ENV,
    dotenv_launch_overrides,
    explicit_reserve_vram_gb,
    launch_env,
    shell_exports,
)

ROOT = Path(__file__).resolve().parents[3]
START_SH = ROOT / "plugins/comfyui/scripts/start.sh"


def test_dotenv_overrides_take_only_launch_keys(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# comment\n"
        "FLASK_PORT=5000\n"
        "GUAARDVARK_COMFYUI_RESERVE_VRAM=5.0\n"
        "export GUAARDVARK_COMFYUI_ATTENTION='ck'\n"
        'GUAARDVARK_COMFYUI_LISTEN="0.0.0.0"\n'
        "GUAARDVARK_COMFYUI_PREVIEW_SIZE=512 # trailing comment\n"
        "GUAARDVARK_COMFYUI_PINNED_MEMORY=\n"
        "not a pair\n"
        "GUAARDVARK_COMFYUI_BAD KEY=1\n"
    )

    assert dotenv_launch_overrides(dotenv) == {
        "GUAARDVARK_COMFYUI_RESERVE_VRAM": "5.0",
        "GUAARDVARK_COMFYUI_ATTENTION": "ck",
        "GUAARDVARK_COMFYUI_LISTEN": "0.0.0.0",
        "GUAARDVARK_COMFYUI_PREVIEW_SIZE": "512",
        "GUAARDVARK_COMFYUI_PINNED_MEMORY": "",
    }
    assert dotenv_launch_overrides(tmp_path / "missing.env") == {}


def test_dotenv_wins_over_the_inherited_environment(tmp_path):
    (tmp_path / ".env").write_text("GUAARDVARK_COMFYUI_RESERVE_VRAM=2.0\n")
    base = {"GUAARDVARK_COMFYUI_RESERVE_VRAM": "5.0", "GUAARDVARK_COMFYUI_ATTENTION": "sage", "PATH": "/bin"}

    env = launch_env(tmp_path, base)

    assert env["GUAARDVARK_COMFYUI_RESERVE_VRAM"] == "2.0"
    assert env["GUAARDVARK_COMFYUI_ATTENTION"] == "sage"  # untouched: not in .env
    assert env["PATH"] == "/bin"
    assert explicit_reserve_vram_gb(env) == 2.0
    assert explicit_reserve_vram_gb({RESERVE_VRAM_ENV: ""}) is None


def test_shell_exports_survive_a_real_bash_eval():
    exports = shell_exports({
        "GUAARDVARK_COMFYUI_ATTENTION": "ck",
        "GUAARDVARK_COMFYUI_LISTEN": "it's \"quoted\" $HOME `x`",
    })
    script = exports + '\nprintf "%s|%s" "$GUAARDVARK_COMFYUI_ATTENTION" "$GUAARDVARK_COMFYUI_LISTEN"'

    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout

    assert out == 'ck|it\'s "quoted" $HOME `x`'


def test_start_sh_snippet_reads_a_fresh_dotenv(tmp_path):
    """Run the exact heredoc start.sh uses, against a temp checkout."""
    text = START_SH.read_text()
    snippet = text.split("<<'DOTENV'\n", 1)[1].split("\nDOTENV\n", 1)[0]
    fake_root = tmp_path / "checkout"
    (fake_root / "backend" / "services").mkdir(parents=True)
    (fake_root / "backend" / "services" / "comfyui_launch_flags.py").write_bytes(
        (ROOT / "backend/services/comfyui_launch_flags.py").read_bytes()
    )
    (fake_root / ".env").write_text("GUAARDVARK_COMFYUI_RESERVE_VRAM=3.5\nGUAARDVARK_COMFYUI_ATTENTION=pytorch\n")

    stale = dict(os.environ, GUAARDVARK_COMFYUI_RESERVE_VRAM="5.0")
    exports = subprocess.run(
        [sys.executable, "-", str(fake_root)], input=snippet, capture_output=True, text=True, check=True,
    ).stdout
    reserve = subprocess.run(
        ["bash", "-c", exports + '\nRESERVE_VRAM="${GUAARDVARK_COMFYUI_RESERVE_VRAM:-1.0}"; printf "%s" "$RESERVE_VRAM"'],
        capture_output=True, text=True, check=True, env=stale,
    ).stdout

    assert reserve == "3.5", "the .env value replaces the stale inherited one"


def test_start_sh_sources_dotenv_before_reading_launch_keys():
    text = START_SH.read_text()
    assert "dotenv_launch_overrides" in text
    sourcing = text.index("dotenv_launch_overrides")
    for key in ("GUAARDVARK_COMFYUI_ATTENTION", "GUAARDVARK_COMFYUI_RESERVE_VRAM",
                "GUAARDVARK_COMFYUI_LISTEN", "GUAARDVARK_COMFYUI_PREVIEW_METHOD",
                "GUAARDVARK_COMFYUI_PINNED_MEMORY"):
        assert key.startswith(LAUNCH_ENV_PREFIX)
        assert text.index(f"${{{key}") > sourcing, f"{key} is read before .env is sourced"
