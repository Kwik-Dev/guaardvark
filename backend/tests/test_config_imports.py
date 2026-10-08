"""backend/config.py is a module of names, not an object called `config`.

`from backend.config import config` raises ImportError. Inside a broad
try/except that error looks like "the probe failed": the stale-job reaper took
it as "ComfyUI is down" on every pass and cancelled every running render.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

BACKEND = Path(__file__).resolve().parents[1]


def test_nothing_imports_a_config_object_from_backend_config():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "venv" in path.parts or "tests" in path.parts:
            continue
        if "from backend.config import config" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == []


def test_backend_config_exposes_the_names_the_watchdog_reads():
    import backend.config as config

    assert isinstance(config.COMFYUI_URL, str) and config.COMFYUI_URL.startswith("http")
    assert config.OUTPUT_DIR


def test_a_running_render_is_counted(tmp_path, monkeypatch):
    import backend.config as config
    from backend.services import plugin_bridge

    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    job = tmp_path / ".progress_jobs" / "render-1"
    job.mkdir(parents=True)
    (job / "metadata.json").write_text(json.dumps({
        "process_type": "video_render",
        "status": "processing",
        "last_update_utc": datetime.now(timezone.utc).isoformat(),
    }))
    assert plugin_bridge._count_active_video_render_jobs() == 1
