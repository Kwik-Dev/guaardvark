"""generate_speech runs as an Audio Foundry job and answers with a job id when
the read outlasts its wait.

It used to wait 110 s on one request, while the plugin's own estimate for the
3000-character maximum is about 100 s before the model load; a long or cold
read reported "did not answer" while the file was still made, with no id to
poll. The backend HTTP calls and the clock are faked; nothing renders.
"""

import pytest

from backend.tools import audio_tools
from backend.tools.audio_tools import GenerateSpeechTool


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_backend(monkeypatch):
    """Answers per (method, path); a list is served one item per call."""
    from backend.utils import backend_http

    answers, calls = {}, []

    def fake_request_json(method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        answer = answers[(method, path)]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return backend_http.BackendResponse(status=200, body=answer, data=answer)

    clock = _Clock()
    monkeypatch.setattr(backend_http, "request_json", fake_request_json)
    monkeypatch.setattr(audio_tools.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(audio_tools.time, "sleep", clock.sleep)
    monkeypatch.setattr(audio_tools, "_speech_wait_s", lambda: 60.0)
    return answers, calls


SUBMIT = ("POST", "/api/audio-foundry/generate/voice")
JOB = "e" * 32
POLL = ("GET", f"/api/audio-foundry/jobs/{JOB}")
DONE = {"id": JOB, "intent": "voice", "status": "done", "progress": {"current": 3, "total": 3},
        "result": {"path": "/srv/x/data/uploads/Audio/v.wav", "duration_s": 12.04, "document_id": 41,
                   "meta": {"backend": "kokoro", "voice": "bm_george"}}}


def test_every_call_asks_for_a_job(fake_backend):
    answers, calls = fake_backend
    answers[SUBMIT] = {"mode": "async", "job_id": JOB, "status": "queued", "estimate_s": 0.4}
    answers[POLL] = DONE
    res = GenerateSpeechTool().execute(text="Hello there.", voice="bm_george")
    assert calls[0][2] == {"text": "Hello there.", "backend": "kokoro", "voice_id": "bm_george",
                           "async": True, "queue": True}
    assert res.success, res.error
    out = res.output
    assert out["status"] == "complete" and out["job_id"] == JOB
    assert out["file"] == "v.wav" and out["document_id"] == 41 and out["duration_s"] == 12.0
    assert out["engine"] == "kokoro" and out["voice"] == "bm_george"
    assert "/srv/x" not in str(out)


def test_a_long_read_answers_with_the_job_id_to_poll(fake_backend):
    answers, calls = fake_backend
    answers[SUBMIT] = {"mode": "async", "job_id": JOB, "status": "queued", "estimate_s": 98.0}
    answers[POLL] = {"id": JOB, "intent": "voice", "status": "running",
                     "progress": {"current": 4, "total": 14, "stage": "synthesizing"}}
    res = GenerateSpeechTool().execute(text="word " * 590)
    assert res.success, res.error
    out = res.output
    assert out["job_id"] == JOB and out["status"] == "running" and out["progress"] == "4/14 parts"
    assert "get_generation_status" in out["next"] and "Do not call" in out["next"]
    polls = [c for c in calls if c[0] == "GET"]
    # Polled every SPEECH_POLL_S for the 60 s wait, never past it.
    assert len(polls) == int(60 / audio_tools.SPEECH_POLL_S)


def test_a_failed_job_is_reported_with_its_reason(fake_backend):
    answers, _ = fake_backend
    answers[SUBMIT] = {"job_id": JOB, "status": "queued"}
    answers[POLL] = [{"status": "running"},
                     {"status": "error", "error": "WeightsNotInstalled: Kokoro voice 'bm_george' is not on this machine."}]
    res = GenerateSpeechTool().execute(text="Hi.", voice="bm_george")
    assert res.success is False
    assert JOB in res.error and "not on this machine" in res.error


def test_a_lost_poll_still_hands_back_the_job(fake_backend):
    from backend.utils.backend_http import BackendError

    answers, _ = fake_backend
    answers[SUBMIT] = {"job_id": JOB, "status": "queued"}
    answers[POLL] = BackendError("timeout", "The Guaardvark backend did not answer")
    res = GenerateSpeechTool().execute(text="Hi.")
    assert res.success and res.output["job_id"] == JOB and res.output["status"] == "queued"


def test_an_inline_answer_from_an_older_plugin_still_completes(fake_backend):
    answers, calls = fake_backend
    answers[SUBMIT] = DONE["result"]
    res = GenerateSpeechTool().execute(text="Hi.")
    assert res.success and res.output["status"] == "complete" and res.output["document_id"] == 41
    assert [c for c in calls if c[0] == "GET"] == []


def test_the_wait_is_the_tool_job_bound():
    from backend.services import tool_jobs

    assert audio_tools._speech_wait_s() == tool_jobs.wait_seconds()
    assert audio_tools._speech_wait_s() <= tool_jobs.MAX_WAIT_S
    assert audio_tools.SPEECH_SUBMIT_TIMEOUT_S < 120
