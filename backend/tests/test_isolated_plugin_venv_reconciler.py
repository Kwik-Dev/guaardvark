from pathlib import Path
from scripts.dep_reconciler.reconcilers.isolated_plugin_venv import IsolatedPluginVenv


def _make_plugin(tmp_path: Path) -> Path:
    plug = tmp_path / "plugins" / "audio_foundry"
    (plug / "scripts").mkdir(parents=True)
    (plug / "requirements.txt").write_text("chatterbox-tts==0.1.7\n")
    (plug / "scripts" / "setup_venv.sh").write_text("#!/bin/bash\nexit 0\n")
    return tmp_path


def test_is_active_true_when_setup_venv_present(tmp_path):
    root = _make_plugin(tmp_path)
    r = IsolatedPluginVenv(root, "audio_foundry")
    assert r.is_active() is True


def test_is_active_false_when_setup_venv_absent(tmp_path):
    # The negative of the primary guard: a plugin without scripts/setup_venv.sh
    # is NOT an isolated-venv plugin and this reconciler must not claim it.
    plug = tmp_path / "plugins" / "no_script_plugin"
    plug.mkdir(parents=True)
    r = IsolatedPluginVenv(tmp_path, "no_script_plugin")
    assert r.is_active() is False


def test_hash_stable_for_identical_inputs(tmp_path, monkeypatch):
    # A non-deterministic hash would make every reconcile run look like drift.
    root = _make_plugin(tmp_path)
    r = IsolatedPluginVenv(root, "audio_foundry")
    import backend.services.hardware_policy as hp
    monkeypatch.setattr(hp, "policy_fingerprint", lambda *_a, **_k: "hwfp:STABLE")
    monkeypatch.setattr(r, "_hardware", lambda: {})
    assert r.compute_hash() == r.compute_hash()


def test_hash_changes_when_fingerprint_changes(tmp_path, monkeypatch):
    root = _make_plugin(tmp_path)
    r = IsolatedPluginVenv(root, "audio_foundry")
    import backend.services.hardware_policy as hp
    monkeypatch.setattr(hp, "policy_fingerprint", lambda *_a, **_k: "hwfp:AAAA")
    monkeypatch.setattr(r, "_hardware", lambda: {})
    h1 = r.compute_hash()
    monkeypatch.setattr(hp, "policy_fingerprint", lambda *_a, **_k: "hwfp:BBBB")
    h2 = r.compute_hash()
    assert h1 != h2   # hardware change -> rebuild
