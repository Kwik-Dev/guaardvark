"""Shared fixtures for self-improvement test suite."""
import os
import shutil
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "sandbox_code")


@pytest.fixture
def sandbox_dir(tmp_path, monkeypatch):
    """Create a temporary sandbox with copies of fixture files.

    Saved-memory recall is stubbed: outside an app context it imports
    backend.app, which boots the whole app inside the test and changes what the
    model is asked (test_agent_memory_hints.py covers recall itself).
    """
    monkeypatch.setattr("backend.api.memory_api.search_memories", lambda *a, **k: [])
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Copy all fixture files into sandbox
    for f in os.listdir(FIXTURES_DIR):
        src = os.path.join(FIXTURES_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, sandbox / f)
    return sandbox


@pytest.fixture
def sandbox_file(sandbox_dir):
    """Create a single temporary Python file for simple tests."""
    p = sandbox_dir / "test_target.py"
    p.write_text('def hello():\n    return "world"\n')
    return p


def ollama_available():
    """Check if Ollama is running and has a model loaded."""
    try:
        import urllib.request
        import json
        resp = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(resp.read())
        return len(data.get("models", [])) > 0
    except Exception:
        return False


_skip_without_llm = pytest.mark.skipif(
    not ollama_available(),
    reason="Ollama not available or no models loaded"
)


def requires_llm(obj):
    """A test that needs a live model: skipped without one, and marked
    integration so a unit run (-m "not integration") leaves it out instead of
    loading the machine's default model."""
    return pytest.mark.integration(_skip_without_llm(obj))
