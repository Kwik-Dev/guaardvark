import pytest
from backend.services.batch_image_generator import BatchImageGenerator, BatchImageRequest, BatchPrompt
from backend.services.offline_image_generator import OfflineImageGenerator

def test_batch_resource_estimates_non_resident():
    # Setup mock OfflineImageGenerator
    gen = OfflineImageGenerator()
    gen._pipeline = None
    gen._current_model = None

    # Instantiate BatchImageGenerator and inject mock generator
    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    prompt = BatchPrompt(
        id="p1",
        prompt="A beautiful cat",
        model="zimage-turbo"
    )
    request = BatchImageRequest(
        batch_id="batch_1",
        prompts=[prompt],
        output_dir="/tmp/test_batch"
    )

    # When the model is NOT resident, it should return full estimates (11000MB VRAM, 24GB RAM)
    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request)
    assert vram_mb == 11000
    assert ram_gb == 24.0

def test_batch_resource_estimates_resident():
    # Setup mock OfflineImageGenerator with resident model
    gen = OfflineImageGenerator()
    gen._pipeline = object() # mock loaded pipeline
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"

    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    prompt = BatchPrompt(
        id="p1",
        prompt="A beautiful cat",
        model="zimage-turbo"
    )
    request = BatchImageRequest(
        batch_id="batch_1",
        prompts=[prompt],
        output_dir="/tmp/test_batch"
    )

    # When the model IS resident, it should return baseline minimum estimates (4000MB VRAM, 6.0GB RAM)
    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request)
    assert vram_mb == 4000
    assert ram_gb == 6.0
