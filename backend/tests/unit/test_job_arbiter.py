"""The job arbiter (services/job_arbiter.py): ranking, safe points, holds, moves.

Every test runs against a private ledger and a fake clock. backend/tests/
conftest.py pins job order off for the whole suite; the ``arbiter`` fixture
switches it on, and the tests in the first section leave it off on purpose.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.services import job_arbiter, job_priority_data as data
from backend.services.job_operation_gate import (
    GpuBusyError,
    GpuQueuedError,
    JobOperationGate,
    _HOLDER_ID_LABELS,
)
from backend.services.job_types import JobKind


class Clock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    led = job_arbiter.Ledger(tmp_path / "job_queue.json")
    monkeypatch.setattr(job_arbiter, "_LEDGER", led)
    monkeypatch.setattr(job_arbiter, "_held_since", {})
    clock = Clock()
    monkeypatch.setattr(job_arbiter, "_clock", clock)
    return SimpleNamespace(ledger=led, clock=clock, path=tmp_path / "job_queue.json")


@pytest.fixture
def arbiter(ledger, monkeypatch):
    """Job order switched on."""
    monkeypatch.setattr(job_arbiter, "enabled", lambda: True)
    monkeypatch.setattr("backend.services.chat_keep_ready._gpu_busy_reason", lambda: None)
    return ledger


def _open(job_type, op_id, **kw):
    return job_arbiter.Admission.open(job_type, op_id, **kw)


def _tickets():
    return job_arbiter._LEDGER.peek()["tickets"]


# --- Job order off: today's behaviour --------------------------------------

def test_off_every_entry_point_is_a_pass_through(ledger):
    def no_sleep(_s):
        raise AssertionError("nothing waits with job order off")

    with job_arbiter.gpu_admission(JobKind.VIDEO_RENDER, "VideoBatch_1", evicts_ollama=True) as adm:
        assert adm is None
    with job_arbiter.interactive("chat", "s1"):
        assert job_arbiter.should_yield("indexing") is False
        assert job_arbiter.yield_point("indexing", 1, sleep=no_sleep) == 0.0
        assert job_arbiter.box_idle() is True
    job_arbiter.record_hold("ollama:gemma4:e4b")
    job_arbiter.drop_hold("ollama:gemma4:e4b")
    snap = job_arbiter.snapshot()
    assert snap["enabled"] is False and snap["queued"] == [] and snap["running"] == []
    with pytest.raises(job_arbiter.QueueError):
        job_arbiter.move("anything", "up")
    with pytest.raises(job_arbiter.QueueError):
        job_arbiter.run_now("anything")
    assert not ledger.path.exists(), "with job order off the ledger is never written"


def test_off_gpu_session_hands_the_gate_no_admit_hook(ledger, monkeypatch):
    seen = {}

    class FakeGate:
        @contextlib.contextmanager
        def gpu_exclusive(self, kind, op_id, **kwargs):
            seen.update(kwargs)
            yield True

        def release_gpu_exclusive(self, *a, **k):
            pass

    monkeypatch.setattr("backend.services.job_operation_gate.get_gate", lambda: FakeGate())
    monkeypatch.setattr("backend.services.gpu_resource_policy.release_kept_image_pipeline", lambda *_a, **_k: False)
    from backend.services.gpu_resource_policy import gpu_session

    with gpu_session(JobKind.VIDEO_RENDER, "VideoBatch_1") as held:
        assert held is True
    assert seen["admit"] is None
    assert not ledger.path.exists()


def test_off_the_gate_refuses_a_second_job_as_before():
    gate = JobOperationGate()
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1"):
        with pytest.raises(GpuBusyError) as refused:
            with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "mv_1_0", admit=None):
                pass
    assert not isinstance(refused.value, GpuQueuedError)
    assert "held by video_render:VideoBatch_1" in str(refused.value)


def _fake_time(monkeypatch, module):
    clock = SimpleNamespace(now=0.0, slept=[])

    def monotonic():
        return clock.now

    def sleep(s):
        clock.slept.append(s)
        clock.now += s

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=monotonic, sleep=sleep, time=lambda: clock.now))
    return clock


def _scripted_sessions(monkeypatch, module, refusals):
    """gpu_session that refuses with each of ``refusals`` in turn, then holds."""
    calls = []

    @contextlib.contextmanager
    def fake_session(kind, op_id, **kwargs):
        calls.append(op_id)
        if len(calls) <= len(refusals):
            raise refusals[len(calls) - 1]
        yield True

    monkeypatch.setattr(module, "gpu_session", fake_session)
    return calls


def test_off_when_free_backs_off_as_before(ledger, monkeypatch):
    from backend.services import gpu_resource_policy as grp

    clock = _fake_time(monkeypatch, grp)
    held_by = "GPU is held by video_render:VideoBatch_A — wait for completion"
    _scripted_sessions(monkeypatch, grp, [GpuBusyError(held_by), GpuBusyError("busy")])
    with grp.gpu_session_when_free(JobKind.TRAINING, "job1", wait_s=600) as held:
        assert held is True
    # 2 s, then 3 s (x1.5), in half-second sleeps: today's backoff.
    assert sum(clock.slept) == pytest.approx(5.0)
    assert not ledger.path.exists()


# --- Ranking ------------------------------------------------------------------

def test_classes_go_interactive_then_yours_then_background(arbiter):
    bg = _open("indexing", "doc1")
    arbiter.clock.advance(1)
    yours = _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(1)
    chat = _open("chat_picture", "chat_edit_1")
    assert bg.turn()[0] is False
    assert yours.turn() == (False, "Queued 2nd, behind Picture for chat")
    assert chat.turn() == (True, "")
    order = [i["job_type"] for i in job_arbiter.snapshot()["queued"]]
    assert order == ["chat_picture", "video_batch", "indexing"]


def test_same_class_is_first_come_first_served(arbiter):
    first = _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(1)
    second = _open("image_batch", "ImageBatch_1")
    assert first.turn()[0] is True
    assert second.turn()[0] is False


def test_a_refused_try_keeps_its_place_and_its_waiting_time(arbiter):
    first = _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(1)
    with pytest.raises(GpuBusyError):
        with job_arbiter.gpu_admission(JobKind.VIDEO_RENDER, "mv_1_0") as adm:
            assert adm.turn()[0] is False
            raise GpuQueuedError("not yet")
    kept = next(t for t in _tickets().values() if t["op_id"] == "mv_1_0")
    arbiter.clock.advance(5)
    again = _open("music_video", "mv_1_0")
    assert again.id == kept["id"]
    assert _tickets()[again.key]["enqueued_at"] == kept["enqueued_at"]
    assert first.turn()[0] is True


def test_a_single_shot_caller_that_is_refused_leaves_the_line(arbiter):
    _open("video_batch", "VideoBatch_1").turn()
    arbiter.clock.advance(1)
    with pytest.raises(GpuQueuedError):
        with job_arbiter.gpu_admission(JobKind.VIDEO_RENDER, "prod_7", polls=False) as stage:
            ok, reason = stage.turn()
            assert ok is False
            raise GpuQueuedError(reason)
    assert [t["op_id"] for t in _tickets().values()] == ["VideoBatch_1"]


def test_a_wait_that_gives_up_leaves_the_line(arbiter, monkeypatch):
    from backend.services import gpu_resource_policy as grp

    _fake_time(monkeypatch, grp)
    _scripted_sessions(monkeypatch, grp, [GpuBusyError("busy")] * 10)
    with pytest.raises(GpuBusyError):
        with grp.gpu_session_when_free(JobKind.VIDEO_RENDER, "upscale:u1", wait_s=3):
            pass
    assert _tickets() == {}


def test_a_job_that_fails_for_good_leaves_the_line(arbiter):
    from backend.services.job_operation_gate import GpuCapacityError

    with pytest.raises(GpuCapacityError):
        with job_arbiter.gpu_admission(JobKind.VIDEO_RENDER, "VideoBatch_1"):
            raise GpuCapacityError("card too small")
    assert _tickets() == {}


def test_a_job_asleep_in_a_backoff_does_not_hold_the_line(arbiter):
    head = _open("video_batch", "VideoBatch_1")
    assert head.turn()[0] is True
    arbiter.clock.advance(1)
    behind = _open("image_batch", "ImageBatch_1")
    assert behind.turn()[0] is False
    arbiter.clock.advance(data.HEAD_FRESH_S + 1)
    assert behind.turn()[0] is True


def test_a_requeueing_caller_keeps_the_head_through_its_countdown(arbiter):
    with pytest.raises(GpuBusyError):
        with job_arbiter.gpu_admission(JobKind.VIDEO_RENDER, "mv_1_0", polls=False) as clip:
            assert clip.turn()[0] is True
            raise GpuBusyError("GPU is held by another process")
    arbiter.clock.advance(1)
    other = _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(11)  # music_video_tasks requeues with countdown=12
    assert other.turn()[0] is False
    arbiter.clock.advance(data.RETRY_FRESH_S)
    assert other.turn()[0] is True


def test_waiting_tickets_nobody_asks_for_are_dropped(arbiter):
    _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(data.WAITING_STALE_S + 1)
    assert _tickets() == {}


def test_tickets_of_a_process_that_is_gone_are_dropped(arbiter):
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    with job_arbiter._LEDGER.transaction() as state:
        state["tickets"]["ghost"] = {
            "key": "ghost", "id": "g", "cls": "yours", "state": "running", "resource": "gpu",
            "pid": proc.pid, "pid_started_at": None, "seen_at": arbiter.clock(),
        }
    assert "ghost" not in _tickets()


def test_a_nested_ledger_transaction_raises_instead_of_deadlocking(arbiter):
    with job_arbiter._LEDGER.transaction():
        with pytest.raises(RuntimeError):
            with job_arbiter._LEDGER.transaction():
                pass


# --- Interactive work and background safe points ----------------------------

def test_background_holds_at_a_safe_point_while_chat_runs(arbiter):
    turn = contextlib.ExitStack()
    turn.enter_context(job_arbiter.interactive("chat", "session1"))
    seen = []

    def sleep(_s):
        snap = job_arbiter.snapshot()
        seen.append([p["job_type"] for p in snap["paused"]])
        arbiter.clock.advance(1)
        if len(seen) == 3:
            turn.close()  # the reply finished
        if len(seen) > 3 + data.INTERACTIVE_GRACE_S + 2:
            raise AssertionError("the hold did not end after the grace")

    held = job_arbiter.yield_point("indexing", 42, sleep=sleep)
    assert seen[0] == ["indexing"], "a held job is listed as paused"
    assert held >= 3 + data.INTERACTIVE_GRACE_S
    assert job_arbiter.snapshot()["paused"] == []


def test_a_cancel_ends_the_hold(arbiter):
    with job_arbiter.interactive("chat", "s1"):
        held = job_arbiter.yield_point("autoresearch", "run1", should_stop=lambda: True,
                                       sleep=lambda _s: pytest.fail("should not sleep"))
    assert held == 0.0


def test_background_resumes_after_the_grace(arbiter):
    with job_arbiter.interactive("chat", "s1"):
        assert job_arbiter.should_yield("indexing") is True
    assert job_arbiter.should_yield("indexing") is True
    arbiter.clock.advance(data.INTERACTIVE_GRACE_S + 0.1)
    assert job_arbiter.should_yield("indexing") is False


def test_only_background_work_holds_for_a_turn(arbiter):
    with job_arbiter.interactive("voice_chat", "s1"):
        assert job_arbiter.should_yield("video_batch") is False
        assert job_arbiter.should_yield("chat_picture") is False
        render = _open("video_batch", "VideoBatch_1")
        assert render.turn()[0] is True, "a render a turn may be waiting on is not held by the turn"
        bg = _open("indexing", "doc1")
        ok, reason = bg.turn()
        assert ok is False and "chatting" in reason


def test_a_turn_that_never_ends_stops_holding_background_work(arbiter):
    with job_arbiter.interactive("chat", "stuck"):
        arbiter.clock.advance(data.INTERACTIVE_MAX_S + 1)
        assert job_arbiter.should_yield("indexing") is False


# --- A running render is never evicted --------------------------------------

def test_a_running_render_keeps_the_gpu_when_chat_asks_for_it(arbiter):
    gate = JobOperationGate()
    render = _open("video_batch", "VideoBatch_1")
    with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1", admit=job_arbiter.admit_callable(render)):
        render.started()
        with job_arbiter.interactive("chat", "s1"):
            picture = _open("chat_picture", "chat_edit_1")
            assert picture.turn()[0] is True, "interactive work heads the line"
            with pytest.raises(GpuBusyError) as refused:
                with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "chat_edit_1",
                                        admit=job_arbiter.admit_callable(picture)):
                    pass
            assert "held by video_render:VideoBatch_1" in str(refused.value)
            assert not isinstance(refused.value, GpuQueuedError)
            assert gate.snapshot()["gpu_holder"]["native_id"] == "VideoBatch_1"
            running = [i["op_id"] for i in job_arbiter.snapshot()["running"]]
            assert "VideoBatch_1" in running
    render.finished()


def test_the_gate_raises_queued_when_it_is_not_this_jobs_turn(arbiter):
    gate = JobOperationGate()
    _open("chat_picture", "chat_edit_1").turn()
    render = _open("video_batch", "VideoBatch_1")
    with pytest.raises(GpuQueuedError) as refused:
        with gate.gpu_exclusive(JobKind.VIDEO_RENDER, "VideoBatch_1", admit=job_arbiter.admit_callable(render)):
            pass
    assert "Queued 2nd" in str(refused.value)
    assert gate.snapshot()["gpu_busy"] is False, "a job refused its turn never touched the gate"


def test_when_free_retries_a_queued_refusal_every_poll(arbiter, monkeypatch):
    from backend.services import gpu_resource_policy as grp

    clock = _fake_time(monkeypatch, grp)
    _scripted_sessions(monkeypatch, grp, [GpuQueuedError("Queued 2nd"), GpuQueuedError("Queued 2nd"),
                                          GpuBusyError("busy")])
    with grp.gpu_session_when_free(JobKind.TRAINING, "job1", wait_s=600) as held:
        assert held is True
    # Two queued refusals at POLL_S each, then the first backoff step (2 s).
    assert sum(clock.slept) == pytest.approx(2 * data.POLL_S + 2.0)


def test_when_free_asks_again_each_poll_while_the_card_is_only_held(arbiter, monkeypatch):
    from backend.services import gpu_resource_policy as grp

    clock = _fake_time(monkeypatch, grp)
    _scripted_sessions(monkeypatch, grp, [
        GpuBusyError("GPU is held by another process (GPU already locked by video_render:x)."),
        GpuBusyError("GPU cooling down — try again in 3.0s"),
        GpuBusyError("Not enough free VRAM for video_render:job1: ... Try again shortly."),
    ])
    with grp.gpu_session_when_free(JobKind.TRAINING, "job1", wait_s=600) as held:
        assert held is True
    # Held and cooling down are asked again each poll; a VRAM refusal backs off.
    assert sum(clock.slept) == pytest.approx(2 * data.POLL_S + 2.0)


# --- Aging ------------------------------------------------------------------

def test_a_waiting_background_job_overtakes_later_yours_work(arbiter):
    def keep_asking(*adms):
        for a in adms:
            a.touch()

    early_render = _open("video_batch", "VideoBatch_0")
    arbiter.clock.advance(1)
    bg = _open("indexing", "doc1")
    arbiter.clock.advance(1)
    late_render = _open("image_batch", "ImageBatch_1")
    order = [i["op_id"] for i in job_arbiter.snapshot()["queued"]]
    assert order == ["VideoBatch_0", "ImageBatch_1", "doc1"]
    step = data.WAITING_STALE_S / 2
    waited = 0.0
    while waited < data.BACKGROUND_AGING_S + 1:
        arbiter.clock.advance(step)
        waited += step
        keep_asking(early_render, bg, late_render)
    order = [i["op_id"] for i in job_arbiter.snapshot()["queued"]]
    assert order == ["VideoBatch_0", "doc1", "ImageBatch_1"]


def test_aging_never_lifts_background_above_interactive(arbiter):
    bg = _open("indexing", "doc1")
    for _ in range(int(3 * data.BACKGROUND_AGING_S / 10)):
        arbiter.clock.advance(10)
        bg.touch()
    _open("chat_picture", "chat_edit_1")
    assert [i["class"] for i in job_arbiter.snapshot()["queued"]] == ["interactive", "background"]


def test_long_held_background_waits_for_the_turn_only(arbiter):
    job_arbiter._held_since["indexing"] = arbiter.clock() - data.BACKGROUND_AGING_S - 1
    with job_arbiter.interactive("chat", "s1"):
        assert job_arbiter.should_yield("indexing") is True
    assert job_arbiter.should_yield("indexing") is False, "no grace once it has waited long enough"


# --- Swarm GPU hold ---------------------------------------------------------

def test_a_render_waits_for_a_swarm_hold_until_run_now(arbiter):
    job_arbiter.record_hold("ollama:gemma4:e4b", holder="A swarm agent")
    render = _open("video_batch", "VideoBatch_1", evicts_ollama=True)
    ok, reason = render.turn()
    assert ok is False and "gemma4:e4b" in reason and "Run now" in reason
    item = job_arbiter.snapshot()["queued"][0]
    assert item["can_run_now"] is True
    job_arbiter.run_now(item["id"])
    assert render.turn()[0] is True


def test_a_hold_does_not_stop_work_that_keeps_ollama(arbiter):
    job_arbiter.record_hold("ollama:gemma4:e4b")
    editor = _open("video_render", "clip_ab12", evicts_ollama=False)
    assert editor.turn()[0] is True


def test_a_chat_picture_is_not_held_by_a_swarm_hold(arbiter):
    job_arbiter.record_hold("ollama:gemma4:e4b")
    picture = _open("chat_picture", "stills_chat_1", evicts_ollama=True)
    assert picture.turn()[0] is True


def test_a_render_held_back_does_not_block_the_one_behind_it(arbiter):
    job_arbiter.record_hold("ollama:gemma4:e4b")
    held = _open("video_batch", "VideoBatch_1", evicts_ollama=True)
    assert held.turn()[0] is False
    editor = _open("video_render", "clip_ab12", evicts_ollama=False)
    assert editor.turn()[0] is True


def test_the_hold_ending_lets_the_render_go(arbiter):
    job_arbiter.record_hold("ollama:gemma4:e4b")
    render = _open("video_batch", "VideoBatch_1", evicts_ollama=True)
    assert render.turn()[0] is False
    job_arbiter.drop_hold("ollama:gemma4:e4b")
    assert render.turn()[0] is True


def test_run_now_is_only_for_an_item_on_a_hold(arbiter):
    render = _open("video_batch", "VideoBatch_1", evicts_ollama=True)
    render.turn()
    with pytest.raises(job_arbiter.QueueError):
        job_arbiter.run_now(render.id)


# --- Moves --------------------------------------------------------------------

def test_the_person_can_move_an_item_up_and_down(arbiter):
    a = _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(1)
    b = _open("image_batch", "ImageBatch_1")
    arbiter.clock.advance(1)
    c = _open("indexing", "doc1")
    snap = job_arbiter.move(c.id, "up")
    assert [i["op_id"] for i in snap["queued"]] == ["VideoBatch_1", "doc1", "ImageBatch_1"]
    snap = job_arbiter.move(c.id, "up")
    assert [i["op_id"] for i in snap["queued"]] == ["doc1", "VideoBatch_1", "ImageBatch_1"]
    assert snap["queued"][0]["moved"] is True
    snap = job_arbiter.move(a.id, "down")
    assert [i["op_id"] for i in snap["queued"]] == ["doc1", "ImageBatch_1", "VideoBatch_1"]
    assert b.turn()[0] is False
    c.touch()
    assert c.turn()[0] is True


def test_a_move_of_an_item_that_left_is_refused(arbiter):
    with pytest.raises(job_arbiter.QueueError):
        job_arbiter.move("nope", "up")


# --- Classification ---------------------------------------------------------

@pytest.mark.parametrize("prefix", [p for p, _label in _HOLDER_ID_LABELS])
def test_every_gpu_holder_prefix_has_a_job_type(prefix):
    job_type = data.job_type_for_session(JobKind.VIDEO_RENDER, f"{prefix}x")
    assert job_type in data.JOB_TYPES and job_type != "gpu_job"


def test_music_video_storyboards_are_not_clips():
    assert data.job_type_for_session(JobKind.VIDEO_RENDER, "mv_storyboards_3") == "storyboard"
    assert data.job_type_for_session(JobKind.VIDEO_RENDER, "mv_storyboard_3_1") == "storyboard"
    assert data.job_type_for_session(JobKind.VIDEO_RENDER, "mv_3_1") == "music_video"
    assert data.requeues("music_video") and not data.requeues("storyboard")


def test_the_editor_render_is_named_by_its_slot():
    assert data.job_type_for_session(JobKind.VIDEO_RENDER, "clip_ab12cd34",
                                     "video_render:editor_clip") == "video_render"


def test_chat_pictures_are_interactive():
    for op in ("stills_chat_ab12", "chat_edit_1", "chat_anim_1", "chat_qwen_edit_1", "chat_pulid_1"):
        assert data.default_class(data.job_type_for_session(JobKind.VIDEO_RENDER, op)).value == "interactive"


def test_training_kinds_fall_back_to_their_kind():
    assert data.job_type_for_session(JobKind.TRAINING, "123") == "training"
    assert data.job_type_for_session(JobKind.LORA_TRAIN, "subject_4") == "lora_train"


# --- gpu_session with job order on ------------------------------------------

def test_gpu_session_takes_a_place_and_leaves_when_done(arbiter, monkeypatch):
    seen = {}

    class FakeGate:
        @contextlib.contextmanager
        def gpu_exclusive(self, kind, op_id, **kwargs):
            seen["admit"] = kwargs["admit"]
            ok, _ = kwargs["admit"]()
            assert ok
            yield True

        def release_gpu_exclusive(self, *a, **k):
            pass

    monkeypatch.setattr("backend.services.job_operation_gate.get_gate", lambda: FakeGate())
    monkeypatch.setattr("backend.services.gpu_resource_policy.release_kept_image_pipeline", lambda *_a, **_k: False)
    from backend.services.gpu_resource_policy import gpu_session

    with gpu_session(JobKind.VIDEO_RENDER, "VideoBatch_1"):
        running = job_arbiter.snapshot()["running"]
        assert [r["job_type"] for r in running] == ["video_batch"]
    assert seen["admit"] is not None
    assert _tickets() == {}


# --- Nightly work -------------------------------------------------------------

def test_nightly_work_waits_for_an_idle_machine(arbiter):
    render = _open("video_batch", "VideoBatch_1")
    assert job_arbiter.box_idle(600) is False
    render.started()
    render.finished()
    assert job_arbiter.box_idle(600) is False, "Yours work ended moments ago"
    arbiter.clock.advance(601)
    assert job_arbiter.box_idle(600) is True


def test_nightly_work_waits_while_gpu_work_runs(arbiter, monkeypatch):
    monkeypatch.setattr("backend.services.chat_keep_ready._gpu_busy_reason", lambda: "ComfyUI is rendering")
    assert job_arbiter.box_idle(0) is False


# --- The setting --------------------------------------------------------------

def test_the_setting_is_off_without_a_row_or_variable(monkeypatch):
    from backend.utils import settings_utils

    monkeypatch.setattr("backend.celery_beat_gates._read_setting", lambda key, default=None, table="settings": None)
    monkeypatch.delenv("GUAARDVARK_JOB_PRIORITY", raising=False)
    monkeypatch.setattr(settings_utils, "_job_priority_seen", {"value": None, "at": 0.0})
    assert settings_utils.get_job_priority_enabled() is False


def test_the_setting_reads_the_row_and_is_kept_for_a_while(monkeypatch):
    from backend.utils import settings_utils

    rows = {"job_priority_enabled": "true"}
    monkeypatch.setattr("backend.celery_beat_gates._read_setting",
                        lambda key, default=None, table="settings": rows.get(key, default))
    monkeypatch.setattr(settings_utils, "_job_priority_seen", {"value": None, "at": 0.0})
    assert settings_utils.get_job_priority_enabled() is True
    rows["job_priority_enabled"] = "false"
    assert settings_utils.get_job_priority_enabled() is True, "cached for JOB_PRIORITY_CACHE_S"
    settings_utils._job_priority_seen["at"] -= settings_utils.JOB_PRIORITY_CACHE_S + 1
    assert settings_utils.get_job_priority_enabled() is False


def test_the_variable_switches_it_on_without_a_row(monkeypatch):
    from backend.utils import settings_utils

    monkeypatch.setattr("backend.celery_beat_gates._read_setting", lambda key, default=None, table="settings": None)
    monkeypatch.setenv("GUAARDVARK_JOB_PRIORITY", "true")
    monkeypatch.setattr(settings_utils, "_job_priority_seen", {"value": None, "at": 0.0})
    assert settings_utils.get_job_priority_enabled() is True


# --- The ledger file ----------------------------------------------------------

def test_the_ledger_is_written_atomically_and_only_on_change(arbiter):
    _open("video_batch", "VideoBatch_1")
    before = os.stat(arbiter.path).st_mtime_ns
    with job_arbiter._LEDGER.transaction():
        pass
    assert os.stat(arbiter.path).st_mtime_ns == before
    leftovers = [p.name for p in arbiter.path.parent.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


# --- HTTP: the swarm's hold, the Activity page's queue ---------------------

def _orchestrator():
    import threading
    from unittest.mock import patch

    from backend.services.gpu_memory_orchestrator import GPUMemoryOrchestrator

    o = object.__new__(GPUMemoryOrchestrator)  # not the process singleton
    o._lock = threading.RLock()
    o._registry = {}
    o._ollama_expiry = {}
    o._eviction_grace_s = 0
    o._idle_timeout_s = 300
    return o, patch.object(o, "_get_vram_info", return_value={"success": False})


def _hold_client(orch):
    from unittest.mock import patch

    from flask import Flask

    from backend.api.gpu_orchestrator_api import gpu_orchestrator_bp

    app = Flask(__name__)
    app.register_blueprint(gpu_orchestrator_bp)
    return app.test_client(), patch("backend.api.gpu_orchestrator_api._get_orch", return_value=orch)


def _hold_and_release(client, slot):
    client.post("/api/gpu/memory/preload", json={"slot_id": slot, "vram_mb": 4000})
    client.post("/api/gpu/memory/mark-loaded", json={"slot_id": slot})
    assert client.post("/api/gpu/memory/begin-use", json={"slot_id": slot}).status_code == 200
    holds_while_pinned = dict(job_arbiter._LEDGER.peek()["holds"])
    client.post("/api/gpu/memory/end-use", json={"slot_id": slot})
    return holds_while_pinned


def test_a_swarm_pin_is_recorded_as_a_hold_and_dropped_when_unpinned(arbiter):
    orch, vram = _orchestrator()
    client, routed = _hold_client(orch)
    with vram, routed:
        holds = _hold_and_release(client, "ollama:gemma4:e4b")
    assert list(holds) == ["ollama:gemma4:e4b"]
    assert job_arbiter._LEDGER.peek()["holds"] == {}


def test_with_job_order_off_a_swarm_pin_writes_nothing(ledger):
    orch, vram = _orchestrator()
    client, routed = _hold_client(orch)
    with vram, routed:
        holds = _hold_and_release(client, "ollama:gemma4:e4b")
    assert holds == {}
    assert not ledger.path.exists()


def _jobs_client():
    from flask import Flask

    from backend.api.unified_jobs_resource_api import unified_jobs_resource_bp

    app = Flask(__name__)
    app.register_blueprint(unified_jobs_resource_bp)
    return app.test_client()


def test_the_queue_route_says_job_order_is_off(ledger):
    client = _jobs_client()
    body = client.get("/api/jobs/queue").get_json()
    assert body["enabled"] is False and body["queued"] == []
    res = client.post("/api/jobs/queue/abc/move", json={"direction": "up"})
    assert res.status_code == 409


def test_the_queue_route_lists_and_moves_with_job_order_on(arbiter):
    _open("video_batch", "VideoBatch_1")
    arbiter.clock.advance(1)
    second = _open("image_batch", "ImageBatch_1")
    client = _jobs_client()
    body = client.get("/api/jobs/queue").get_json()
    assert [i["position"] for i in body["queued"]] == [1, 2]
    moved = client.post(f"/api/jobs/queue/{second.id}/move", json={"direction": "up"}).get_json()
    assert moved["queued"][0]["op_id"] == "ImageBatch_1"
    assert client.post(f"/api/jobs/queue/{second.id}/run-now").status_code == 409
