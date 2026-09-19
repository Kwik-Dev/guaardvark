"""A batch image's recorded size is the size of the file, and a chosen canvas is kept.

ImageBatch 2026-09-12 (one prompt, zimage-turbo, 960x544, style artistic):
metadata said 960x544, the PNG was 1024x1024. The prompt ended "no text, no
letters, no typography, no signage"; the shared keyword detector read that as
a request for on-image text and the offline generator enlarged the canvas to
1024x1024 "for legible type", while the batch metadata copied the requested
size. A 12-prompt batch with the same fields, whose rewrite dropped the
phrase, produced 960x544.
"""

from types import SimpleNamespace

from PIL import Image

from backend.services import stills_pipeline
from backend.services.batch_image_generator import BatchImageGenerator, BatchPrompt
from backend.services.offline_image_generator import OfflineImageGenerator
from backend.services.stills_pipeline import StillResult

INCIDENT_PROMPT = (
    "a single dark computer tower standing alone at the bottom edge of an endless neon grid plain, "
    "cyberpunk synthwave key art, wide 16:9, the centre of the frame is empty negative space, "
    "no text, no letters, no typography, no signage"
)
INCIDENT_REWRITE = (
    "The framing is a wide 16:9 cinematic shot, with the center of the frame occupied by empty "
    "negative space. High contrast lighting illuminates the textures of the metal, with no text "
    "or typography visible."
)


def _intent(prompt):
    return OfflineImageGenerator._has_text_intent(OfflineImageGenerator, prompt)


def test_ruling_text_out_is_not_asking_for_text():
    assert _intent(INCIDENT_PROMPT) is False
    assert _intent(INCIDENT_REWRITE) is False
    assert _intent("clean product shot, without any visible logos or labels") is False
    assert _intent("a poster, avoid text, no watermark") is False


def test_asking_for_text_still_counts():
    assert _intent('a neon sign that reads "OPEN"') is True
    assert _intent("a logo with the letters 'ACME' on the wall") is True
    assert _intent('a storefront sign that reads "BAKERY", no other text anywhere') is True


def test_only_the_legacy_placeholder_canvas_is_enlarged():
    assert OfflineImageGenerator._text_canvas(512, 512, "zimage") == (1024, 1024)
    assert OfflineImageGenerator._text_canvas(448, 512, "sdxl") == (1024, 1024)
    assert OfflineImageGenerator._text_canvas(960, 544, "zimage") == (960, 544)
    assert OfflineImageGenerator._text_canvas(768, 768, "krea2") == (768, 768)
    assert OfflineImageGenerator._text_canvas(512, 512, "sd") == (512, 512)


def test_metadata_records_the_file_and_the_generator_gets_the_request(tmp_path, monkeypatch):
    asked = []

    def fake_stills(prompts, **kw):
        asked.append((kw["width"], kw["height"]))
        rendered = tmp_path / "render.png"
        Image.new("RGB", (1024, 1024)).save(rendered)  # what the generator actually wrote
        return [StillResult(
            success=True, image_path=str(rendered), seed_used=7, model_used="Tongyi-MAI/Z-Image-Turbo",
            prompt_used=prompts[0], width=kw["width"], height=kw["height"], steps=9,
        )]

    monkeypatch.setattr(stills_pipeline, "run_stills_pipeline", fake_stills)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = None
    gen._should_use_comfy_stills = lambda prompt: False
    output_dir = tmp_path / "ImageBatch_test"
    (output_dir / "images").mkdir(parents=True)
    prompt = BatchPrompt(id="prompt_1", prompt=INCIDENT_PROMPT, style="artistic",
                         width=960, height=544, model="zimage-turbo", auto_enhance=True)
    status = SimpleNamespace(generate_thumbnails=False, completed_images=0, total_images=1)

    result = gen._generate_single_image("ImageBatch_test", prompt, output_dir, status)

    assert result.success is True
    assert asked == [(960, 544)], "the generator is asked for the prompt's canvas"
    assert result.metadata["dimensions"] == "1024x1024"
    assert result.metadata["dimensions_requested"] == "960x544"
    with Image.open(result.image_path) as img:
        assert f"{img.size[0]}x{img.size[1]}" == result.metadata["dimensions"]


def test_matching_file_records_no_requested_size(tmp_path, monkeypatch):
    def fake_stills(prompts, **kw):
        rendered = tmp_path / "render.png"
        Image.new("RGB", (kw["width"], kw["height"])).save(rendered)
        return [StillResult(success=True, image_path=str(rendered), width=kw["width"], height=kw["height"])]

    monkeypatch.setattr(stills_pipeline, "run_stills_pipeline", fake_stills)
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.progress_system = None
    gen._should_use_comfy_stills = lambda prompt: False
    output_dir = tmp_path / "ImageBatch_ok"
    (output_dir / "images").mkdir(parents=True)
    prompt = BatchPrompt(id="prompt_1", prompt="a cat", width=960, height=544)
    status = SimpleNamespace(generate_thumbnails=False, completed_images=0, total_images=1)

    result = gen._generate_single_image("ImageBatch_ok", prompt, output_dir, status)

    assert result.metadata["dimensions"] == "960x544"
    assert "dimensions_requested" not in result.metadata
