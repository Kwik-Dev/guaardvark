"""Tests for the cinematic quality wiring in the batch video generator.

Covers the pure/near-pure pieces without spinning up the full generator or a GPU:
the T2V->I2V model mapping and the director pass (prompt rewrite + double-enhance off).
Both are called unbound with a dummy ``self`` so no queue worker / ComfyUI is needed."""

from types import SimpleNamespace

from backend.services.batch_video_generator import (
    BatchVideoGenerator,
    BatchVideoRequest,
    BatchVideoItem,
)


def test_to_i2v_model_mapping():
    assert BatchVideoGenerator._to_i2v_model("wan22-14b") == "wan22-14b-i2v"
    assert BatchVideoGenerator._to_i2v_model("cogvideox-5b") == "cogvideox-5b-i2v"
    assert BatchVideoGenerator._to_i2v_model("wan22-14b-i2v") == "wan22-14b-i2v"  # already I2V
    assert BatchVideoGenerator._to_i2v_model(None) == "wan22-14b-i2v"            # safe default


def _req(items, **kw):
    return BatchVideoRequest(batch_id="b1", items=items, output_dir="/tmp/x", **kw)


def test_apply_director_rewrites_prompts_and_disables_double_enhance(monkeypatch):
    import backend.services.video_director as vd
    monkeypatch.setattr(vd, "direct_prompts", lambda prompts, **k: [f"DIR:{p}" for p in prompts])

    items = [BatchVideoItem(id="1", prompt="a"), BatchVideoItem(id="2", prompt="b")]
    req = _req(items, director_mode=True, enhance_prompt=True, prompt_style="cinematic")

    BatchVideoGenerator._apply_director(SimpleNamespace(), req)

    assert [it.prompt for it in items] == ["DIR:a", "DIR:b"]
    # director output is already complete -> downstream light enhancer is turned off
    assert req.enhance_prompt is False


def test_apply_director_skips_image_only_items(monkeypatch):
    import backend.services.video_director as vd
    seen = {}

    def fake_direct(prompts, **k):
        seen["prompts"] = list(prompts)
        return [f"DIR:{p}" for p in prompts]

    monkeypatch.setattr(vd, "direct_prompts", fake_direct)

    items = [
        BatchVideoItem(id="1", prompt="text one"),
        BatchVideoItem(id="2", prompt=None, image_path="/img/a.png"),  # image-only, no prompt
    ]
    req = _req(items, director_mode=True)
    BatchVideoGenerator._apply_director(SimpleNamespace(), req)

    # only the text item was sent to the director; image-only item left untouched
    assert seen["prompts"] == ["text one"]
    assert items[0].prompt == "DIR:text one"
    assert items[1].prompt is None


def test_apply_director_never_raises_on_director_failure(monkeypatch):
    import backend.services.video_director as vd

    def boom(prompts, **k):
        raise RuntimeError("director exploded")

    monkeypatch.setattr(vd, "direct_prompts", boom)
    items = [BatchVideoItem(id="1", prompt="a")]
    req = _req(items, director_mode=True, enhance_prompt=True)

    # must not raise; prompts and enhance flag unchanged on failure
    BatchVideoGenerator._apply_director(SimpleNamespace(), req)
    assert items[0].prompt == "a"
    assert req.enhance_prompt is True
