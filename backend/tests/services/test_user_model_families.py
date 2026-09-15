"""Family table: URL parse, match, src sanitise, duplicates."""

from __future__ import annotations

import pytest

from backend.services import user_model_families as umf


def test_parse_hf_url_variants():
    assert umf.parse_hf_url("https://huggingface.co/Comfy-Org/MiniMax-H3") == {
        "hf_repo": "Comfy-Org/MiniMax-H3", "revision": "main", "src": None,
    }
    assert umf.parse_hf_url("https://hf.co/org/repo")["hf_repo"] == "org/repo"
    blob = umf.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/blob/main/loras/x.safetensors"
    )
    assert blob["src"] == "loras/x.safetensors"
    resolve = umf.parse_hf_url(
        "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/loras/x.safetensors?download=true"
    )
    assert resolve["src"] == "loras/x.safetensors"
    tree = umf.parse_hf_url("https://huggingface.co/org/repo/tree/v1.2")
    assert tree["hf_repo"] == "org/repo"
    assert tree["revision"] == "v1.2"
    assert tree["src"] is None
    blob_rev = umf.parse_hf_url(
        "https://huggingface.co/org/repo/blob/v1.2/weights/model.safetensors"
    )
    assert blob_rev["revision"] == "v1.2"
    assert blob_rev["src"] == "weights/model.safetensors"
    pr = umf.parse_hf_url(
        "https://huggingface.co/org/repo/tree/refs/pr/12"
    )
    assert pr["revision"] == "refs/pr/12" and pr["src"] is None
    pr_file = umf.parse_hf_url(
        "https://huggingface.co/org/repo/blob/refs/pr/1/loras/x.safetensors"
    )
    assert pr_file["revision"] == "refs/pr/1"
    assert pr_file["src"] == "loras/x.safetensors"
    branch = umf.parse_hf_url(
        "https://huggingface.co/org/repo/resolve/refs/heads/dev/weights/a.safetensors"
    )
    assert branch["revision"] == "refs/heads/dev"
    assert branch["src"] == "weights/a.safetensors"
    assert umf.parse_hf_url("Comfy-Org/MiniMax-H3")["hf_repo"] == "Comfy-Org/MiniMax-H3"
    assert umf.hf_inspect_url("org/repo") == "https://huggingface.co/org/repo"
    assert umf.hf_inspect_url("org/repo", "v1.2") == "https://huggingface.co/org/repo/tree/v1.2"
    assert umf.hf_inspect_url("org/repo", "main", "weights/a.safetensors") == (
        "https://huggingface.co/org/repo/blob/main/weights/a.safetensors"
    )
    with pytest.raises(ValueError, match="Hugging Face"):
        umf.parse_hf_url("https://civitai.com/models/1")
    with pytest.raises(ValueError, match="dataset"):
        umf.parse_hf_url("https://huggingface.co/datasets/org/repo")
    with pytest.raises(ValueError, match="Space"):
        umf.parse_hf_url("https://huggingface.co/spaces/org/app")
    with pytest.raises(ValueError, match="org/repo"):
        umf.parse_hf_url("only-one-token")


def test_sanitize_repo_src_rejects_parent_dir():
    with pytest.raises(ValueError, match="inside the repo"):
        umf.sanitize_repo_src("../evil.safetensors")
    with pytest.raises(ValueError, match="inside the repo"):
        umf.sanitize_repo_src("foo/../../etc/passwd")
    assert umf.sanitize_repo_src("loras/a.safetensors") == "loras/a.safetensors"


def test_match_flux_not_sdxl():
    matches = umf.match_families(
        domain="image", files=[{"src": "flux1-dev.safetensors"}], src=None,
        hf_repo="black-forest-labs/FLUX.1-dev", has_model_index=False,
    )
    assert matches[0]["family"] == "flux" and matches[0]["wired"] is True
    assert matches[0]["role"] == "generation"


def test_match_lora_filename():
    matches = umf.match_families(
        domain="image", files=[], src="my_lora.safetensors", hf_repo="x/y", has_model_index=False,
    )
    assert matches[0]["role"] == "lora" and matches[0]["family"] == "zimage"


def test_match_flux_lora_not_zimage():
    matches = umf.match_families(
        domain="image",
        files=[{"src": "flux-dev-lora.safetensors"}],
        src=None,
        hf_repo="someone/flux-style-lora",
        has_model_index=False,
    )
    assert matches[0]["role"] == "lora" and matches[0]["family"] == "flux"


def test_match_wan_moe_pair():
    matches = umf.match_families(
        domain="video",
        files=[
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_high_lighting_v2.0.safetensors"},
            {"src": "NSFW/Wan2.2_Remix_NSFW_i2v_14b_low_lighting_v2.0.safetensors"},
        ],
        src=None, hf_repo="FX-FeiHou/wan2.2-Remix",
    )
    assert matches[0]["role"] == "generation"
    assert matches[0]["like"] == "wan22-14b-i2v"
    assert matches[0]["moe"] is True


def test_match_unwired_qwen_image():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="Qwen/Qwen-Image",
        has_model_index=True, pipeline_tag="qwen-image",
    )
    assert matches[0]["wired"] is False
    assert matches[0]["family"] == "qwen-image"
    assert "not wired" in matches[0]["reason"]


def test_match_index_class_flux_without_filename_tokens():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="someone/mystery-stills",
        has_model_index=True, index_class="diffusers.FluxPipeline",
    )
    assert matches[0]["family"] == "flux" and matches[0]["wired"] is True
    assert "class:FluxPipeline" in matches[0]["reason"]


def test_match_index_class_qwen_without_name_tokens():
    matches = umf.match_families(
        domain="image", files=[], src=None, hf_repo="org/untitled",
        has_model_index=True, index_class="QwenImagePipeline",
    )
    assert matches[0]["wired"] is False
    assert matches[0]["family"] == "qwen-image"


def test_find_duplicate():
    catalog = {"models": {
        "user-sdxl-a": {
            "hf_repo": "x/y", "revision": "main",
            "files": [{"src": "merged.safetensors"}], "kind": "single_file",
        }
    }}
    assert umf.find_duplicate(
        catalog, hf_repo="x/y", revision="main",
        files=[{"src": "merged.safetensors"}],
    ) == "user-sdxl-a"
    assert umf.find_duplicate(
        catalog, hf_repo="x/y", revision="main",
        files=[{"src": "other.safetensors"}],
    ) is None
