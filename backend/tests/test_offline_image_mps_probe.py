"""The MPS probe must not be able to kill the backend (2026-10-05).

Metal rejects a double-committed command buffer by calling ``__assert_rtn`` and aborting
the process with SIGABRT. That is a native abort from the Objective-C runtime, not a
Python exception, so the ``try: ... except Exception`` the probe used to sit inside could
never catch it: "this Mac's MPS is broken" became "the whole backend is dead, mid-request,
taking chat, jobs and every other in-flight call with it". Three crash reports in two days
carried one signature, all landing in ``OfflineImageGenerator.__init__``:

    abort -> __assert_rtn -> MTLReportFailure -> -[IOGPUMetalCommandBuffer validate]
    -> -[AGXG17XFamilyCommandBuffer commit] -> at::mps::MPSStream::synchronize
    -> MPSModule_deviceSynchronize -> cfunction_vectorcall_NOARGS -> slot_tp_init

These tests pin both halves of the fix without needing a broken Mac:

* the verdict rules, including "a signal death is an answer, not a crash";
* that the parent process really does survive a child that aborts (a real SIGABRT, via
  ``os.abort``) or hangs;
* that device selection falls back to CPU and *says why* instead of dying.

They do not prove this Mac's MPS works — see ``test_the_real_probe_returns_a_verdict``,
which asserts only that the probe answers at all.
"""
from __future__ import annotations

import functools

import pytest

from backend.services import offline_image_generator as oig


@pytest.fixture(autouse=True)
def no_cached_verdict(monkeypatch):
    """`mps_usable()` caches module-globally; keep tests from inheriting each other."""
    monkeypatch.setattr(oig, "_mps_probe_result", None)


@pytest.fixture
def probe_log(monkeypatch):
    """Capture this module's own log lines.

    A spy rather than `caplog` on purpose: this file must not depend on the app's logging
    configuration (handlers, propagation) to observe its own behaviour, and the log line is
    the user-visible half of the fallback — "this machine is on CPU now, and here is why" —
    which is exactly what was missing when the crash took two days to find.
    """
    messages: list = []

    class _Spy:
        def _record(self, msg, *_a, **_k):
            messages.append(str(msg))

        warning = info = error = debug = _record

    monkeypatch.setattr(oig, "logger", _Spy())
    return messages


# --- the verdict rules -----------------------------------------------------

def test_a_healthy_probe_is_usable():
    assert oig._interpret_probe(0, "ok\n", "") == (True, "ok")


def test_a_probe_killed_by_a_signal_is_a_verdict_not_an_exception():
    """The exact stderr from the crash reports, and the exact returncode of SIGABRT."""
    metal = (
        "-[IOGPUMetalCommandBuffer validate]:214: failed assertion "
        "`commit an already committed command buffer'"
    )
    usable, detail = oig._interpret_probe(-6, "", metal + "\n")

    assert usable is False
    assert "signal 6" in detail
    assert "failed assertion" in detail, "the Metal message must survive into the log"


def test_a_clean_refusal_keeps_the_childs_reason():
    assert oig._interpret_probe(4, "MPS is not available\n", "") == (False, "MPS is not available")


def test_a_silent_failure_still_says_something():
    usable, detail = oig._interpret_probe(1, "", "")
    assert usable is False and "code 1" in detail


def test_stderr_noise_does_not_hide_the_verdict():
    """torch prints warnings to stderr; a late one must not outrank the child's own verdict."""
    usable, detail = oig._interpret_probe(0, "ok\n", "WARNING: torch noise after the verdict\n")

    assert usable is True
    assert detail == "ok"


# --- the child boundary ----------------------------------------------------

def test_the_parent_survives_a_child_that_aborts(monkeypatch):
    """The regression itself: SIGABRT in the probe must not be SIGABRT here.

    A real abort, not a simulated returncode — `os.abort()` raises SIGABRT in the child
    exactly as Metal's assertion does.
    """
    monkeypatch.setattr(oig, "MPS_PROBE_SOURCE", "import os; os.abort()")

    usable, detail = oig.probe_mps_in_subprocess(timeout=60)

    assert usable is False
    assert "signal" in detail


def test_the_parent_survives_a_child_that_hangs(monkeypatch):
    monkeypatch.setattr(oig, "MPS_PROBE_SOURCE", "import time; time.sleep(60)")

    usable, detail = oig.probe_mps_in_subprocess(timeout=1.0)

    assert usable is False
    assert "did not finish" in detail


def test_the_verdict_is_computed_once_per_process(monkeypatch):
    """The child pays a torch import, so it must not run per construction."""
    calls = []

    def counted(**kwargs):
        calls.append(kwargs)
        return (True, "ok")

    monkeypatch.setattr(oig, "probe_mps_in_subprocess", counted)

    assert oig.mps_usable() == (True, "ok")
    assert oig.mps_usable() == (True, "ok")
    assert len(calls) == 1


def test_the_real_probe_returns_a_verdict():
    """End-to-end: whatever this machine's MPS does, the probe answers instead of aborting.

    Asserts only that a verdict comes back — not which one, because that is the machine's
    business. The point is that a broken MPS build is survivable.
    """
    usable, detail = oig.probe_mps_in_subprocess(timeout=oig.MPS_PROBE_TIMEOUT)

    assert isinstance(usable, bool)
    assert isinstance(detail, str) and detail
    if usable:
        assert detail == "ok"


# --- device selection ------------------------------------------------------

@pytest.fixture
def accelerator(monkeypatch):
    """Control `torch`'s CUDA/MPS probes without touching the real hardware.

    `functools.wraps` keeps ``__wrapped__`` on the stand-ins, because torch's dynamo reads
    it off both probes when it builds its trace rules (same reason as
    test_offline_video_mps.py).
    """
    monkeypatch.setattr(oig, "diffusion_available", True, raising=False)
    real = oig.torch

    def _set(*, cuda: bool, mps: bool):
        monkeypatch.setattr(
            real.cuda, "is_available",
            functools.wraps(real.cuda.is_available)(lambda: cuda),
        )
        monkeypatch.setattr(
            real.backends.mps, "is_available",
            functools.wraps(real.backends.mps.is_available)(lambda: mps),
            raising=False,
        )

    return _set


class _Tensor:
    """Just enough tensor for the device probe: ``zeros(1) + zeros(1)``."""

    def __add__(self, other):
        return self


def _torn_down_driver(*_args, **_kwargs):
    raise RuntimeError("driver torn down")


def test_mps_is_selected_when_its_probe_says_usable(monkeypatch, accelerator):
    accelerator(cuda=False, mps=True)
    monkeypatch.setattr(oig, "mps_usable", lambda: (True, "ok"))

    assert oig._select_device() == ("mps", "MPS is usable")


def test_a_failed_probe_falls_back_to_cpu_and_says_why(monkeypatch, accelerator, probe_log):
    accelerator(cuda=False, mps=True)
    monkeypatch.setattr(
        oig, "mps_usable",
        lambda: (False, "the probe process was killed by signal 6: failed assertion"),
    )

    device, reason = oig._select_device()

    assert device == "cpu"
    assert "signal 6" in reason
    assert any("signal 6" in m for m in probe_log), (
        "a silent fallback to CPU is what made this hard to find"
    )


def test_cuda_still_wins_when_it_probes_clean(monkeypatch, accelerator):
    """Regression guard for the MPS refactor: CUDA selection is unchanged.

    The real ``torch.zeros(..., device='cuda')`` cannot succeed on this Mac, so the CUDA
    probe is stubbed — this pins the selection logic, not the hardware. A Mac-only test
    that skipped itself would not guard anything.
    """
    accelerator(cuda=True, mps=True)
    monkeypatch.setattr(oig.torch, "zeros", lambda *a, **k: _Tensor())
    monkeypatch.setattr(oig.torch.cuda, "synchronize", lambda: None)

    assert oig._select_device() == ("cuda", "CUDA is usable")


def test_a_cuda_probe_that_fails_degrades_to_cpu(monkeypatch, accelerator, probe_log):
    """A CUDA error *is* catchable (unlike Metal's abort), and still falls back."""
    accelerator(cuda=True, mps=False)
    monkeypatch.setattr(oig.torch, "zeros", _torn_down_driver)

    device, reason = oig._select_device()

    assert device == "cpu"
    assert "CUDA" in reason
    assert any("driver torn down" in m for m in probe_log)


def test_without_diffusion_dependencies_the_device_is_cpu(monkeypatch):
    """The old code called `torch.cuda.is_available()` unguarded, so a box with no torch
    raised NameError out of the constructor. Same convention as the status route."""
    monkeypatch.setattr(oig, "diffusion_available", False, raising=False)

    device, reason = oig._select_device()

    assert device == "cpu"
    assert "not installed" in reason


def test_the_constructor_uses_the_probe_and_reports_the_reason(monkeypatch, accelerator, probe_log):
    """The wiring, not just the helper: __init__ must take its device from _select_device."""
    accelerator(cuda=False, mps=True)
    monkeypatch.setattr(
        oig, "mps_usable", lambda: (False, "the probe process was killed by signal 6")
    )

    gen = oig.OfflineImageGenerator()

    assert gen._device == "cpu"
    assert any("signal 6" in m for m in probe_log)
    assert any("Device: cpu" in m for m in probe_log)
