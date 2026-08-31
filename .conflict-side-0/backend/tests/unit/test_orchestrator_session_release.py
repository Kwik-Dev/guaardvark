"""Session bookings expire on release without evicting resident model slots."""

import pytest

from backend.services import gpu_memory_orchestrator
from backend.services.gpu_resource_policy import _orchestrator_release


@pytest.mark.parametrize(
    ("slot_id", "drop_booking"),
    [
        ("image_batch:ImageBatch_x", True),
        ("video_render:batch_y", True),
        ("legacy:VIDEO_pipeline", True),
        ("sd:pipeline", False),
        ("ollama:llm", False),
    ],
)
def test_orchestrator_session_release(monkeypatch, slot_id, drop_booking):
    calls = []

    class FakeOrchestrator:
        def release_model(self, released_slot_id):
            calls.append(("release_model", released_slot_id))

        def drop_booking(self, dropped_slot_id):
            calls.append(("drop_booking", dropped_slot_id))

    orchestrator = FakeOrchestrator()
    monkeypatch.setattr(
        gpu_memory_orchestrator, "get_orchestrator", lambda: orchestrator
    )

    _orchestrator_release(slot_id)

    expected = [("release_model", slot_id)]
    if drop_booking:
        expected.append(("drop_booking", slot_id))
    assert calls == expected
