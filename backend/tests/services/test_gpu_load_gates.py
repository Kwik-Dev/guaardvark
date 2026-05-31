"""Tests for the GPU/load admission gates (D1/D2/D3/F2).

NO real GPU, NO model loads, NO rendering — everything uses a fresh in-memory
JobOperationGate, a fresh GlobalLoadGate, and monkeypatched psutil/nvidia-smi.
"""
from __future__ import annotations

import pytest


# ----------------------------------------------------------------------------
# D2 — gpu_exclusive contextmanager + GPU_EXCLUSIVE_KINDS
# ----------------------------------------------------------------------------

def _fresh_gate():
    from backend.services.job_operation_gate import JobOperationGate
    return JobOperationGate()


def test_lora_train_in_gpu_exclusive_kinds():
    from backend.services.job_operation_gate import GPU_EXCLUSIVE_KINDS
    from backend.services.job_types import JobKind
    assert JobKind.LORA_TRAIN in GPU_EXCLUSIVE_KINDS
    assert JobKind.TRAINING in GPU_EXCLUSIVE_KINDS
    assert JobKind.VIDEO_RENDER in GPU_EXCLUSIVE_KINDS


def test_gpu_exclusive_acquires_and_releases():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1") as acquired:
        assert acquired is True
        assert gate.snapshot()["gpu_busy"] is True
    assert gate.snapshot()["gpu_busy"] is False


def test_gpu_exclusive_second_claim_raises_gpu_busy():
    from backend.services.job_operation_gate import GpuBusyError
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1"):
        with pytest.raises(GpuBusyError):
            with gate.gpu_exclusive(JobKind.TRAINING, "t1"):
                pass  # pragma: no cover — must never enter


def test_gpu_exclusive_releases_on_exception():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with pytest.raises(RuntimeError, match="boom"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "r1"):
            raise RuntimeError("boom")
    # Slot freed despite the exception.
    assert gate.snapshot()["gpu_busy"] is False


def test_gpu_exclusive_on_busy_register_degrades():
    """on_busy='register' runs WITHOUT real exclusivity (degraded visibility)."""
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "holder"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "second", on_busy="register") as acq:
            assert acq is False  # degraded — did not get exclusivity
            snap = gate.snapshot()
            # The original holder still owns the exclusive slot.
            assert snap["gpu_holder"]["native_id"] == "holder"
            # But the degraded job is visible in in_progress.
            assert "second" in snap["in_progress"].get("video_render", [])


def test_gpu_exclusive_idempotent_release_after_register():
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "holder"):
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "second", on_busy="register"):
            pass
        # second's register flag dropped on exit; holder still held.
        snap = gate.snapshot()
        assert "second" not in snap["in_progress"].get("video_render", [])
        assert snap["gpu_holder"]["native_id"] == "holder"


def test_gpu_exclusive_same_holder_idempotent():
    """Re-claiming the exact same kind+id is idempotent (already-holding)."""
    from backend.services.job_types import JobKind
    gate = _fresh_gate()
    ok1, _ = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r1")
    ok2, reason = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r1")
    assert ok1 and ok2
    assert "Already holding" in reason
    # A DIFFERENT id of the same kind must be refused (regression: the old
    # worktree bug compared native_id to itself and wrongly granted it).
    ok3, reason3 = gate.try_claim_gpu_exclusive(JobKind.VIDEO_RENDER, "r2")
    assert ok3 is False
    assert "held by" in reason3


# ----------------------------------------------------------------------------
# D2 — a representative surface: run_storyboard_artist acquires + releases
# (storyboard image-gen is the simplest GPU surface to drive — it takes an
# injectable image_generator, so no model load / ComfyUI / DB writes needed).
# ----------------------------------------------------------------------------

def test_run_storyboard_artist_uses_gpu_gate(monkeypatch):
    """run_storyboard_artist must claim the GPU exclusive slot around the
    generate loop and release it afterwards. The injected image_generator
    asserts the gate is held mid-generation; we assert it's free after."""
    import backend.tasks.production_swarm_tasks as pst
    from backend.services.job_operation_gate import get_gate
    from backend.services.job_types import JobKind

    gate = get_gate()
    gate.release_gpu_exclusive(JobKind.VIDEO_RENDER, "storyboard_555")
    assert gate.snapshot()["gpu_busy"] is False

    seen = {}

    class _Ctx:
        class production:
            id = 555
            name = "SB Prod"

    from contextlib import contextmanager

    @contextmanager
    def _fake_agent_run(prod_id, *, agent_name, expected_stage, next_agent):
        yield _Ctx()

    monkeypatch.setattr(pst, "_agent_run", _fake_agent_run)

    class _Shot:
        shot_number = 1
        description = "a shot"
        shot_subjects = []
        storyboard_image_path = None

    class _Query:
        def filter_by(self, **kw):
            return self
        def all(self):
            return [_Shot()]

    # Replace the whole ProductionShot NAME in the module with a fake class
    # carrying a plain `query` attr — avoids touching SQLAlchemy's real query
    # descriptor (whose read requires a Flask app context).
    class _FakeProductionShot:
        query = _Query()

    monkeypatch.setattr(pst, "ProductionShot", _FakeProductionShot)
    # Fake `db` so db.session.commit() doesn't require a Flask app context.
    class _FakeSession:
        def commit(self):
            pass
    monkeypatch.setattr(pst, "db", type("DB", (), {"session": _FakeSession()})())
    monkeypatch.setattr(pst, "_storyboard_path", lambda prod_id, n: "/tmp/sb.png")

    class _Gen:
        def generate_image(self, **kw):
            seen["held_during_gen"] = gate.snapshot()["gpu_busy"]
            return "/tmp/sb.png"

    pst.run_storyboard_artist(555, image_generator=_Gen())

    assert seen.get("held_during_gen") is True, "gate must be held during generation"
    assert gate.snapshot()["gpu_busy"] is False, "gate must be released after generation"


# ----------------------------------------------------------------------------
# D3 — GlobalLoadGate
# ----------------------------------------------------------------------------

class _VM:
    def __init__(self, avail_gb):
        self.available = int(avail_gb * (1024 ** 3))


class _SW:
    def __init__(self, used_gb):
        self.used = int(used_gb * (1024 ** 3))


def _patch_load(monkeypatch, *, ram_avail_gb, swap_used_gb=0.0, load1=1.0, vram_free_gb=None):
    import backend.services.system_load_gate as slg
    monkeypatch.setattr(slg.psutil, "virtual_memory", lambda: _VM(ram_avail_gb))
    monkeypatch.setattr(slg.psutil, "swap_memory", lambda: _SW(swap_used_gb))
    monkeypatch.setattr(slg.os, "getloadavg", lambda: (load1, load1, load1))
    monkeypatch.setattr(slg, "_read_vram_free_gb", lambda: vram_free_gb)


def test_load_gate_admits_when_healthy(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight
    _patch_load(monkeypatch, ram_avail_gb=40.0)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=4.0), timeout=0.0)  # must not raise
    g.release(JobWeight(ram_gb=4.0))


def test_load_gate_blocks_when_ram_low(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=5.0)  # below 6 GB hard floor
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0), timeout=0.0)


def test_load_gate_blocks_on_swap(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=40.0, swap_used_gb=2.0)  # >1 GB swap
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0), timeout=0.0)


def test_load_gate_reserved_ram_accounting(monkeypatch):
    """Admitting one job reserves its RAM; a second admit sees less headroom."""
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    # 10 GB available; floor is 6 GB. First 3 GB job admits (10-3=7 >= 6).
    _patch_load(monkeypatch, ram_avail_gb=10.0)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    reading = g.read()
    assert reading.reserved_ram_gb == pytest.approx(3.0)
    # A second 3 GB job: effective avail = 10 - 3(reserved) = 7; 7 - 3 = 4 < 6 floor -> blocked.
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    # After releasing the first, the second fits again.
    g.release(JobWeight(ram_gb=3.0))
    g.admit(JobWeight(ram_gb=3.0), timeout=0.0)
    g.release(JobWeight(ram_gb=3.0))


def test_load_gate_vram_unknown_does_not_block(monkeypatch):
    """nvidia-smi missing -> VRAM unknown -> must NOT block on VRAM."""
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight
    _patch_load(monkeypatch, ram_avail_gb=40.0, vram_free_gb=None)
    g = GlobalLoadGate()
    g.admit(JobWeight(ram_gb=1.0, vram_gb=12.0), timeout=0.0)  # would block if VRAM known-low
    g.release(JobWeight(ram_gb=1.0, vram_gb=12.0))


def test_load_gate_vram_known_low_blocks(monkeypatch):
    from backend.services.system_load_gate import GlobalLoadGate, JobWeight, LoadGateTimeout
    _patch_load(monkeypatch, ram_avail_gb=40.0, vram_free_gb=1.0)  # below 1.5 floor
    g = GlobalLoadGate()
    with pytest.raises(LoadGateTimeout):
        g.admit(JobWeight(ram_gb=1.0, vram_gb=0.0), timeout=0.0)


def test_read_vram_free_gb_missing_nvidia_smi(monkeypatch):
    """Direct test that a missing nvidia-smi degrades to None, not an exception."""
    import backend.services.system_load_gate as slg

    def _raise(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(slg.subprocess, "run", _raise)
    assert slg._read_vram_free_gb() is None


def test_system_load_admit_contextmanager_releases(monkeypatch):
    from backend.services.system_load_gate import system_load_admit, JobWeight, get_load_gate
    _patch_load(monkeypatch, ram_avail_gb=40.0)
    g = get_load_gate()
    before = g.read().reserved_ram_gb
    with system_load_admit(JobWeight(ram_gb=2.0), timeout=0.0):
        assert g.read().reserved_ram_gb == pytest.approx(before + 2.0)
    assert g.read().reserved_ram_gb == pytest.approx(before)


# ----------------------------------------------------------------------------
# D3 — swarm orchestrator freeze-guard
# ----------------------------------------------------------------------------

def test_swarm_freeze_guard_blocks_on_low_ram(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMlow:
        available = int(5.0 * (1024 ** 3))  # below 6 GB floor

    class _SWok:
        used = 0

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMlow())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWok())
    reason = orch._spawn_freeze_guard_block_reason()
    assert reason is not None and "RAM" in reason


def test_swarm_freeze_guard_blocks_on_swap(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMok:
        available = int(40.0 * (1024 ** 3))

    class _SWhigh:
        used = int(2.0 * (1024 ** 3))

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMok())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWhigh())
    reason = orch._spawn_freeze_guard_block_reason()
    assert reason is not None and "swap" in reason


def test_swarm_freeze_guard_allows_when_healthy(monkeypatch):
    import plugins.swarm.service.orchestrator as orch

    class _VMok:
        available = int(40.0 * (1024 ** 3))

    class _SWok:
        used = 0

    monkeypatch.setattr(orch.psutil, "virtual_memory", lambda: _VMok())
    monkeypatch.setattr(orch.psutil, "swap_memory", lambda: _SWok())
    assert orch._spawn_freeze_guard_block_reason() is None


# ----------------------------------------------------------------------------
# F2 — casting orphan: run_casting_director is never dispatched; casting still
# advances through production_api.confirm_casting.
# ----------------------------------------------------------------------------

def test_casting_stage_never_auto_dispatched():
    """STAGE_TO_AGENT['casting'] is None and the screenwriter does not chain to
    casting — so run_casting_director never fires automatically (no unprompted
    GPU training)."""
    from backend.services.production_service import STAGE_TO_AGENT
    assert STAGE_TO_AGENT["casting"] is None


def test_casting_director_action_is_advisory_only():
    """The CastingAction.action field exists for the recommendation message but
    run_casting_director must NOT apply it (only voice_id). Guard against a
    regression that starts wiring `action`."""
    import inspect
    import backend.tasks.production_swarm_tasks as pst
    src = inspect.getsource(pst.run_casting_director)
    # voice_id IS applied:
    assert "subj.voice_id = action.voice_id" in src
    # action is NOT used to dispatch GPU training from within the casting
    # director (only the cheap voice_id assignment is applied).
    assert "train_lora" not in src
    assert "_dispatch_lora_train" not in src
    # The applied lines only touch voice_id / ProductionSubject linkage — never
    # action.action or existing_lora_id.
    assert "action.action" not in src
    assert "existing_lora_id" not in src


def test_no_casting_director_send_task_anywhere():
    """No code path enqueues production.run_casting_director."""
    import inspect
    import backend.tasks.production_swarm_tasks as pst
    full = inspect.getsource(pst)
    assert 'send_task(f"production.run_{next_agent}"' in full  # generic chain exists
    # but casting is never a next_agent (screenwriter -> next_agent=None).
    assert 'next_agent="casting' not in full
    assert "next_agent='casting" not in full
