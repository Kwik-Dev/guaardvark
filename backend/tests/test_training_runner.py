"""The trainer runs as its own process: without Hugging Face tokens and with
the Hub offline, in the job's folder, its event lines delivered in order and
everything else in train.log, stopped (with its children) on request or at
its time limit, and a failure reported with the end of its log. stop_trainer
signals only a process that is a trainer.

The trainer here is a small Python script named finetune_model.py written to
tmp_path; nothing loads a model or touches the GPU or the network."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services import training_runner as tr
from backend.services.training.scripts.finetune_model import EVENT_PREFIX

FAKE = '''
import json, os, sys, time
prefix = {prefix!r}
spec = json.load(open(sys.argv[3]))
mode = spec.get("mode", "ok")

def event(name, **fields):
    print(prefix + json.dumps(dict(event=name, **fields)), flush=True)

print("loading the model", flush=True)
event("env", token=os.environ.get("HF_TOKEN"), hub_token=os.environ.get("HUGGING_FACE_HUB_TOKEN"),
      offline=os.environ.get("HF_HUB_OFFLINE"), transformers=os.environ.get("TRANSFORMERS_OFFLINE"),
      datasets=os.environ.get("HF_DATASETS_OFFLINE"), telemetry=os.environ.get("HF_HUB_DISABLE_TELEMETRY"),
      cwd=os.getcwd(), argv=sys.argv[1:3])
if mode == "ok":
    event("progress", step=1, total=2, loss=1.5)
    print(prefix + "not json", flush=True)
    event("progress", step=2, total=2, loss=1.2)
    event("done", model_dir=os.getcwd())
elif mode == "crash":
    print("Traceback (most recent call last):", flush=True)
    print("RuntimeError: CUDA out of memory", flush=True)
    sys.exit(3)
elif mode == "no_result":
    pass
elif mode == "hang":
    child = os.fork()
    if child == 0:
        time.sleep(60)
        os._exit(0)
    event("progress", step=1, total=100, loss=2.0, child=child)
    time.sleep(60)
'''


@pytest.fixture
def script(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    path = folder / "finetune_model.py"
    path.write_text(FAKE.format(prefix=EVENT_PREFIX))
    return path


def _run(script, tmp_path, mode, **kwargs):
    events = []
    kwargs.setdefault("on_event", events.append)
    result = tr.run("run", {"mode": mode}, workdir=tmp_path / "job", script=script, poll_s=0.1,
                    grace_s=2, **kwargs)
    return result, events


def test_the_environment_carries_no_hub_token_and_is_offline(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_secret")
    env = tr.trainer_env()
    assert "HF_TOKEN" not in env and "HUGGING_FACE_HUB_TOKEN" not in env
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        assert env[name] == "1"
    assert env["WANDB_MODE"] == "disabled"
    assert os.environ["HF_TOKEN"] == "hf_secret"


def test_a_blank_cuda_mask_from_the_worker_is_dropped(monkeypatch):
    # Celery workers blank CUDA_VISIBLE_DEVICES for their own CPU work; the
    # trainer must still see the GPU.
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert "CUDA_VISIBLE_DEVICES" not in tr.trainer_env()
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert tr.trainer_env()["CUDA_VISIBLE_DEVICES"] == "1"


def test_web_requests_from_the_trainer_go_nowhere():
    env = tr.trainer_env({})
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[name] == "http://127.0.0.1:9"
    assert "127.0.0.1" in env["NO_PROXY"] and "localhost" in env["NO_PROXY"]


def test_no_gpu_stops_the_trainer_before_unsloth_loads(monkeypatch):
    import torch
    from backend.services.training.scripts import finetune_model

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setitem(sys.modules, "unsloth", None)  # importing it would fail loudly
    with pytest.raises(RuntimeError, match="No CUDA GPU is visible"):
        finetune_model.require_cuda()


def test_a_run_delivers_events_in_order_and_logs_the_rest(script, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_secret")
    started = []

    result, events = _run(script, tmp_path, "ok", on_start=started.append)

    workdir = tmp_path / "job"
    assert result == {"event": "done", "model_dir": str(workdir)}
    env = events[0]
    assert env["token"] is None and env["hub_token"] is None
    assert (env["offline"], env["transformers"], env["datasets"], env["telemetry"]) == ("1", "1", "1", "1")
    assert env["cwd"] == str(workdir)
    assert env["argv"] == ["run", "--spec"]
    assert [e["step"] for e in events if e["event"] == "progress"] == [1, 2]
    assert len(started) == 1 and started[0] > 0
    log = (workdir / tr.LOG_NAME).read_text()
    assert "loading the model" in log and "not json" in log
    assert EVENT_PREFIX + "{" not in log
    assert json.loads((workdir / "run_spec.json").read_text()) == {"mode": "ok"}


def test_a_crash_is_reported_with_the_end_of_the_log(script, tmp_path):
    with pytest.raises(tr.TrainerFailed) as failed:
        _run(script, tmp_path, "crash")
    assert "exited with code 3: RuntimeError: CUDA out of memory" in str(failed.value)
    assert "Traceback" in failed.value.log_tail


def test_a_run_without_a_result_is_a_failure(script, tmp_path):
    with pytest.raises(tr.TrainerFailed, match="without reporting its result"):
        _run(script, tmp_path, "no_result")


def _gone(pid, within=5.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not tr._alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_a_stop_request_ends_the_trainer_and_its_children(script, tmp_path):
    events, started = [], []
    began = time.monotonic()

    with pytest.raises(tr.TrainerStopped):
        tr.run("run", {"mode": "hang"}, workdir=tmp_path / "job", script=script, poll_s=0.1, grace_s=2,
               on_event=events.append, on_start=started.append,
               should_stop=lambda: any(e["event"] == "progress" for e in events))

    assert time.monotonic() - began < 20
    child = next(e["child"] for e in events if e["event"] == "progress")
    assert _gone(started[0]) and _gone(child)


def test_the_time_limit_stops_a_trainer_that_runs_on(script, tmp_path):
    with pytest.raises(tr.TrainerFailed, match="time limit"):
        _run(script, tmp_path, "hang", time_limit_s=1)


def test_stop_trainer_signals_only_a_trainer(script, tmp_path):
    import subprocess

    assert tr.stop_trainer(os.getpid()) is False
    assert tr.stop_trainer(None) is False and tr.stop_trainer("x") is False

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"mode": "hang"}))
    proc = subprocess.Popen([sys.executable, str(script), "run", "--spec", str(spec)],
                            cwd=str(tmp_path), stdout=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not tr._is_trainer(proc.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert tr.stop_trainer(proc.pid, grace_s=2) is True
        assert proc.wait(timeout=5) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
