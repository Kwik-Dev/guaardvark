"""The video render tasks must import — otherwise Celery silently drops them.

2026-08-28: video_text_overlay replaced _DEFAULT_FONT with resolve_font_path(),
video_timeline_render kept importing the old name, and celery_app logged
"Could not import video render tasks" while every timeline render quietly
never ran. An import error in this chain is a feature outage, not a warning.
"""
import importlib


def test_video_render_task_chain_imports():
    import backend.services.video_timeline_render as vtr
    import backend.tasks.video_render_tasks as vrt
    importlib.reload(vtr)
    importlib.reload(vrt)
    assert callable(getattr(vrt, "create_video_render_tasks", None))


def test_drawtext_filter_uses_a_font_that_exists(monkeypatch):
    from backend.services import video_text_overlay as vto
    from backend.services.video_timeline_render import _build_drawtext_filter
    monkeypatch.setattr(vto, "resolve_font_path", lambda: "/fonts/Bold.ttf")
    import backend.services.video_timeline_render as vtr
    monkeypatch.setattr(vtr, "resolve_font_path", lambda: "/fonts/Bold.ttf")
    flt = _build_drawtext_filter({"text": "hello", "fontSize": 40}, "[v0]", "[v1]")
    assert "fontfile=/fonts/Bold.ttf" in flt


def _filter(monkeypatch, element):
    import backend.services.video_timeline_render as vtr
    monkeypatch.setattr(vtr, "resolve_font_path", lambda: "/fonts/Bold.ttf")
    return vtr._build_drawtext_filter(element, "[v0]", "[v1]")


def test_a_named_position_becomes_a_frame_size_independent_expression(monkeypatch):
    """`position` exists because a caption needs the bottom of the frame and neither the CLI
    nor a fixed pixel default knows how big the frame is. Centring needs the rendered text
    width, which only drawtext knows, so the placement must be an expression."""
    flt = _filter(monkeypatch, {"text": "a line", "position": "bottom-center"})

    assert "x=(w-text_w)/2" in flt
    assert "y=h-th-40" in flt
    assert "x=320" not in flt and "y=240" not in flt


def test_without_a_position_the_pixel_defaults_are_untouched(monkeypatch):
    """The Studio sends dragged coordinates; this change must not move anything there."""
    assert "x=320" in _filter(monkeypatch, {"text": "a line"})
    assert "y=240" in _filter(monkeypatch, {"text": "a line"})
    dragged = _filter(monkeypatch, {"text": "a line", "x": 12, "y": 34})
    assert "x=12" in dragged and "y=34" in dragged


def test_an_unknown_position_falls_back_to_the_pixels(monkeypatch):
    """A name the renderer does not know must not silently become something else."""
    flt = _filter(monkeypatch, {"text": "a line", "position": "sideways", "x": 11, "y": 22})

    assert "x=11" in flt and "y=22" in flt


def test_every_named_position_the_api_advertises_is_implemented(monkeypatch):
    from backend.services.video_timeline_render import _POSITION_EXPRS
    from backend.api.video_overlay_api import _VALID_POSITIONS

    assert set(_POSITION_EXPRS) == set(_VALID_POSITIONS), (
        "the renderer and the /text route must accept the same placement vocabulary"
    )
