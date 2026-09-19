"""An image_batch:<id> booking is released through the batch generator.

backend.log (2026-09-12) repeated "Failed to unload image_batch:ImageBatch_…"
while a video render loaded against the weights a finished keyframe batch
left resident. The orchestrator's unload of that slot now asks the batch
generator: a running batch keeps its booking, a finished one has its idle
pipeline unloaded and the booking dropped, and a finished batch drops its
own booking without waiting for the session to exit.
"""

from types import SimpleNamespace

from backend.services import batch_image_generator as big
from backend.services import gpu_memory_orchestrator as gmo
from backend.services.batch_image_generator import BatchImageGenerator
from backend.services.gpu_memory_orchestrator import (
    GPUMemoryOrchestrator, ModelSlot, ModelType, SlotState,
)


def _slot(batch_id="ImageBatch_1"):
    return ModelSlot(
        slot_id=f"image_batch:{batch_id}", model_type=ModelType.IMAGE_BATCH,
        vram_mb=11000, state=SlotState.LOADED,
    )


def _orchestrator_with(slot):
    fake = SimpleNamespace(_registry={slot.slot_id: slot})
    fake._unload_image_batch = lambda s: GPUMemoryOrchestrator._unload_image_batch(fake, s)
    return fake


def _generator(monkeypatch, statuses, released=None):
    gen = BatchImageGenerator.__new__(BatchImageGenerator)
    gen.active_batches = {bid: SimpleNamespace(status=st) for bid, st in statuses.items()}
    gen.batch_lock = __import__("threading").Lock()
    released = released if released is not None else []
    gen._cleanup_gpu_memory = lambda: released.append("pipeline")
    monkeypatch.setattr(big, "_batch_generator_instance", gen)
    return gen, released


def test_running_batch_keeps_its_booking(monkeypatch):
    _generator(monkeypatch, {"ImageBatch_1": "running"})
    slot = _slot()
    orch = _orchestrator_with(slot)

    assert GPUMemoryOrchestrator._unload_model(orch, slot) is False
    assert slot.slot_id in orch._registry
    assert slot.state is SlotState.LOADED


def test_finished_batch_releases_pipeline_and_drops_booking(monkeypatch):
    _, released = _generator(monkeypatch, {"ImageBatch_1": "completed"})
    slot = _slot()
    orch = _orchestrator_with(slot)

    assert GPUMemoryOrchestrator._unload_model(orch, slot) is True
    assert slot.slot_id not in orch._registry
    assert released == ["pipeline"]


def test_stale_booking_never_unloads_under_another_running_batch(monkeypatch):
    _, released = _generator(monkeypatch, {"ImageBatch_1": "completed", "ImageBatch_2": "running"})
    slot = _slot("ImageBatch_1")
    orch = _orchestrator_with(slot)

    assert GPUMemoryOrchestrator._unload_model(orch, slot) is True
    assert released == [], "batch 2 is mid-render on that pipeline"


def test_no_generator_in_this_process_means_the_booking_is_stale(monkeypatch):
    monkeypatch.setattr(big, "_batch_generator_instance", None)
    slot = _slot()
    orch = _orchestrator_with(slot)

    assert GPUMemoryOrchestrator._unload_model(orch, slot) is True
    assert orch._registry == {}


def test_finished_batch_drops_its_own_booking(monkeypatch):
    dropped = []
    monkeypatch.setattr(
        gmo, "get_orchestrator_if_created",
        lambda: SimpleNamespace(drop_booking=dropped.append),
    )
    gen = BatchImageGenerator.__new__(BatchImageGenerator)

    gen._release_batch_booking("ImageBatch_9")

    assert dropped == ["image_batch:ImageBatch_9"]
