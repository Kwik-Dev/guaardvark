"""BatchImageGenerator factories forward every batch-level kwarg the API
produces — in particular ``ui_config``, which "Adjust & Retry" restores from.

Uses a real BatchImageGenerator pointed at a temp directory; the queue worker
thread idles and nothing is generated.
"""
from __future__ import annotations

import json

import pytest

from backend.services.batch_image_generator import (
    BatchImageGenerator,
    BatchImageRequest,
    _BATCH_REQUEST_PARAMS,
)


@pytest.fixture
def gen(tmp_path):
    g = BatchImageGenerator()
    g.base_output_dir = tmp_path
    return g


def test_batch_request_params_track_dataclass_fields():
    assert "ui_config" in _BATCH_REQUEST_PARAMS
    assert "director_mode" in _BATCH_REQUEST_PARAMS
    assert "prompts" not in _BATCH_REQUEST_PARAMS
    assert "output_dir" not in _BATCH_REQUEST_PARAMS
    # Every field on the dataclass is either carried or one of the three
    # positional inputs, so adding a field can't silently be dropped again.
    fields = set(BatchImageRequest.__dataclass_fields__)
    assert fields - _BATCH_REQUEST_PARAMS == {"batch_id", "prompts", "output_dir"}


def test_ui_config_round_trips_into_retry_data(gen):
    ui_config = {"inputMode": "single", "castSubjectIds": [3], "selectedPreset": "landscape"}
    req = gen.create_batch_from_prompts(
        ["a lighthouse at dusk"],
        model="zimage-turbo",
        ui_config=ui_config,
        preserve_order=False,
        generate_thumbnails=False,
    )
    assert req.ui_config == ui_config
    assert req.preserve_order is False
    assert req.generate_thumbnails is False

    from backend.services.batch_image_generator import BatchGenerationStatus
    status = BatchGenerationStatus(
        batch_id=req.batch_id,
        status="completed",
        total_images=1,
        completed_images=1,
        failed_images=0,
        retry_data={"params": {"ui_config": req.ui_config}, "prompts": ["a lighthouse at dusk"]},
    )
    gen._save_batch_metadata(status, gen.base_output_dir / req.batch_id)
    saved = json.loads((gen.base_output_dir / req.batch_id / "batch_metadata.json").read_text())
    assert saved["retry_data"]["params"]["ui_config"] == ui_config


def test_csv_factory_carries_ui_config(gen):
    req = gen.create_batch_from_csv("prompt\nhero on rooftop\n", ui_config={"quantity": 2})
    assert req.ui_config == {"quantity": 2}


def test_find_batch_status_reads_metadata_from_disk(gen):
    req = gen.create_batch_from_prompts(["x"], model="zimage-turbo")
    from backend.services.batch_image_generator import BatchGenerationStatus, BatchImageResult
    status = BatchGenerationStatus(
        batch_id=req.batch_id, status="completed", total_images=1, completed_images=1,
        failed_images=0, display_name="Test", results=[BatchImageResult(prompt_id="p1", success=True, image_path="/x/a.png")],
    )
    gen._save_batch_metadata(status, gen.base_output_dir / req.batch_id)

    assert gen.get_batch_status(req.batch_id) is None  # never queued
    found = gen.find_batch_status(req.batch_id)
    assert found is not None and found.display_name == "Test"
    assert found.results == []
    with_results = gen.find_batch_status(req.batch_id, include_results=True)
    assert [r.prompt_id for r in with_results.results] == ["p1"]
    assert gen.find_batch_status("nope") is None
