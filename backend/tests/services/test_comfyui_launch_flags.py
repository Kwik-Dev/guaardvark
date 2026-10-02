"""The attention backend flag: opt-in, availability-gated, lockstep with start.sh."""
from pathlib import Path

from backend.services.comfyui_launch_flags import (
    ATTENTION_DEFAULT,
    ATTENTION_ENV,
    RESERVE_VRAM_ENV,
    attention_cli_args,
    reserve_vram_cli_args,
)


def test_default_is_pytorch_even_when_kernels_are_available():
    assert ATTENTION_DEFAULT == "pytorch"
    assert attention_cli_args({}, ck_available=True, sage_available=True) == []


def test_explicit_backend_needs_its_package():
    assert attention_cli_args({ATTENTION_ENV: "ck"}, ck_available=True) == ["--use-ck-attention"]
    assert attention_cli_args({ATTENTION_ENV: "ck"}, ck_available=False) == []
    assert attention_cli_args({ATTENTION_ENV: "sage"}, sage_available=True) == ["--use-sage-attention"]
    assert attention_cli_args({ATTENTION_ENV: "sage"}, sage_available=False, ck_available=True) == []


def test_auto_prefers_kitchen_then_sage_then_nothing():
    assert attention_cli_args({ATTENTION_ENV: "auto"}, ck_available=True, sage_available=True) == ["--use-ck-attention"]
    assert attention_cli_args({ATTENTION_ENV: "auto"}, ck_available=False, sage_available=True) == ["--use-sage-attention"]
    assert attention_cli_args({ATTENTION_ENV: "AUTO "}, ck_available=False, sage_available=False) == []


def test_unknown_value_falls_back_to_default():
    assert attention_cli_args({ATTENTION_ENV: "flash"}, ck_available=True, sage_available=True) == []


def test_start_sh_matches_python_helper():
    root = Path(__file__).resolve().parents[3]
    text = (root / "plugins/comfyui/scripts/start.sh").read_text()
    assert ATTENTION_ENV in text
    assert "--use-ck-attention" in text and "--use-sage-attention" in text
    assert "int8_attention_is_available" in text
    assert "import sageattention" in text
    assert ":-pytorch}" in text  # same default as ATTENTION_DEFAULT
    # The launch line, not the earlier "main.py exists" check.
    assert "$ATTN_FLAG" in text.split('"$VENV_PYTHON" main.py')[1].split("\n")[0]


def test_reserve_vram_defaults_and_overrides():
    assert reserve_vram_cli_args({}) == ["--reserve-vram", "1"]
    assert reserve_vram_cli_args({RESERVE_VRAM_ENV: "3.0"}) == ["--reserve-vram", "3"]
    assert reserve_vram_cli_args({RESERVE_VRAM_ENV: "2.5"}) == ["--reserve-vram", "2.5"]
    assert reserve_vram_cli_args({RESERVE_VRAM_ENV: "lots"}) == ["--reserve-vram", "1"]
    assert reserve_vram_cli_args({RESERVE_VRAM_ENV: "-4"}) == ["--reserve-vram", "1"]


def test_start_sh_reserve_vram_matches_python_helper():
    root = Path(__file__).resolve().parents[3]
    text = (root / "plugins/comfyui/scripts/start.sh").read_text()
    assert RESERVE_VRAM_ENV in text
    assert ':-1.0}' in text
    assert '--reserve-vram "$RESERVE_VRAM"' in text


def test_cast_loras_are_on_comfyui_search_path(tmp_path):
    from backend.config import STORAGE_DIR
    from backend.services.comfyui_launch_flags import MODEL_PATHS_ENV, model_paths_launch
    root = Path(__file__).resolve().parents[3]
    args, env = model_paths_launch(root)
    cfg = root / "plugins/comfyui/guaardvark_model_paths.yaml"
    assert args == ["--extra-model-paths-config", str(cfg)]
    assert env == {MODEL_PATHS_ENV: str(Path(STORAGE_DIR) / "training")}
    assert "${" + MODEL_PATHS_ENV + "}" in cfg.read_text()
    assert model_paths_launch(tmp_path) == ([], {})
    start_sh = (root / "plugins/comfyui/scripts/start.sh").read_text()
    assert f"export {MODEL_PATHS_ENV}=" in start_sh
    assert '--extra-model-paths-config "$PLUGIN_ROOT/guaardvark_model_paths.yaml"' in start_sh
