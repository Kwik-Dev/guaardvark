"""Two Install requests at once: the second answers with the running install
instead of hanging on _install_lock, and only one download starts."""
import threading

import pytest

from backend.utils import reranker


@pytest.fixture
def slow_install(monkeypatch):
    started, release = [], threading.Event()
    monkeypatch.setattr(reranker, "is_installed", lambda: False)
    monkeypatch.setattr(reranker, "_run_install", lambda name: (started.append(name), release.wait(5)))
    monkeypatch.setattr(reranker, "_install", {"state": "idle", "model": None, "progress": None,
                                               "downloaded_gb": 0.0, "error": None})
    yield started
    release.set()


def _call(results, i):
    results[i] = reranker.start_install()


def test_two_concurrent_installs_both_answer_and_one_download_runs(slow_install):
    results = [None, None]
    threads = [threading.Thread(target=_call, args=(results, i), daemon=True) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads), "an Install request hung on _install_lock"
    assert [r["install"]["state"] for r in results] == ["running", "running"]
    assert len(slow_install) == 1


def test_install_while_one_runs_answers_at_once(slow_install):
    first = threading.Thread(target=reranker.start_install, daemon=True)
    first.start()
    first.join(timeout=5)
    assert not first.is_alive()
    second = []
    t = threading.Thread(target=lambda: second.append(reranker.start_install()), daemon=True)
    t.start()
    t.join(timeout=5)
    assert second and second[0]["install"]["state"] == "running"
    assert len(slow_install) == 1
