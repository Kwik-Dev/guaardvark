import pytest
from backend.services.batch_image_generator import BatchImageGenerator, BatchImageRequest, BatchPrompt
from backend.services.offline_image_generator import OfflineImageGenerator
import backend.services.offline_image_generator as oig

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

    # When the model is NOT resident, it should return the full family estimates
    # (11000MB VRAM; RAM from _FAMILY_RAM_GB — 21 GB since 2f0f522, measured).
    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request)
    assert vram_mb == 11000
    assert ram_gb == oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]

def test_batch_resource_estimates_resident():
    # Setup mock OfflineImageGenerator with a model resident wholly on the card
    gen = OfflineImageGenerator()
    gen._pipeline = object() # mock loaded pipeline
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._pipeline_offload_mode = "full"

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


def test_batch_resource_estimates_2048_raises_above_flat_constant():
    # 2026-08-04: a 2048² prompt must raise the batch booking above the flat
    # 1024²-calibrated constants (11000MB VRAM / _FAMILY_RAM_GB RAM for zimage).
    gen = OfflineImageGenerator()
    gen._pipeline = None
    gen._current_model = None

    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    prompt = BatchPrompt(
        id="p1",
        prompt="A beautiful cat",
        model="zimage-turbo",
        width=2048,
        height=2048,
    )
    request = BatchImageRequest(
        batch_id="batch_2k",
        prompts=[prompt],
        output_dir="/tmp/test_batch"
    )

    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request)
    assert vram_mb == 11000 + 3 * 500
    base = oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]
    slope = oig.OfflineImageGenerator._FAMILY_RAM_SLOPE_GB_PER_MP.get("zimage", 1.0)
    assert ram_gb == base + 3 * slope


def test_batch_resource_estimates_resident_still_prices_2048_surcharge():
    # Resident pipeline: weights are already on the box, but the >1MP activation
    # surcharge still applies to the booking.
    gen = OfflineImageGenerator()
    gen._pipeline = object()
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._pipeline_offload_mode = "full"

    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    prompt = BatchPrompt(
        id="p1",
        prompt="A beautiful cat",
        model="zimage-turbo",
        width=2048,
        height=2048,
    )
    request = BatchImageRequest(
        batch_id="batch_2k_resident",
        prompts=[prompt],
        output_dir="/tmp/test_batch"
    )

    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request)
    assert vram_mb == max(4000, 1024 + 3 * 500)
    assert ram_gb == max(6.0, 2.0 + 3 * 1.0)


def _zimage_request(batch_id):
    return BatchImageRequest(
        batch_id=batch_id,
        prompts=[BatchPrompt(id="p1", prompt="A beautiful cat", model="zimage-turbo")],
        output_dir="/tmp/test_batch",
    )


def test_batch_resource_estimates_resident_offloaded_needs_full_vram():
    # An offloaded pipeline (sequential/model CPU offload, Z-Image on 16GB cards)
    # keeps its weights in RAM: RAM is discounted, but the card must hold the
    # full working peak again, so another resident (an Ollama model) is evicted.
    gen = OfflineImageGenerator()
    gen._pipeline = object()
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._pipeline_offload_mode = "sequential"

    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    vram_mb, ram_gb = batch_gen._batch_resource_estimates(_zimage_request("batch_offloaded"))
    assert vram_mb == 11000
    assert ram_gb == 6.0


def test_batch_resource_estimates_kept_model_not_reused_gets_no_discount():
    # A model kept after the last batch that this batch will not render with is
    # unloaded before admission, so the batch is priced as a fresh load.
    gen = OfflineImageGenerator()
    gen._pipeline = object()
    gen._current_model = "Tongyi-MAI/Z-Image-Turbo"
    gen._pipeline_offload_mode = "full"
    gen._kept_until = 4102444800.0  # kept (year 2100)

    batch_gen = BatchImageGenerator()
    batch_gen.image_generator = gen

    request = _zimage_request("batch_kept_released")
    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request, reuse_model=None)
    assert vram_mb == 11000
    assert ram_gb == oig.OfflineImageGenerator._FAMILY_RAM_GB["zimage"]

    # The same batch reusing it keeps the discount.
    assert batch_gen._kept_model_for_batch(request) == "Tongyi-MAI/Z-Image-Turbo"
    vram_mb, ram_gb = batch_gen._batch_resource_estimates(request, reuse_model="Tongyi-MAI/Z-Image-Turbo")
    assert vram_mb == 4000
    assert ram_gb == 6.0
