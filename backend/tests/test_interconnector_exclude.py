"""Regression tests for the Interconnector file-sync exclude matching.

Bug (2026-06-03): directory exclude patterns like 'build/' / 'data/' / 'env/' were
matched as bare SUBSTRINGS against the whole path, so 'build/' silently dropped
frontend/src/components/videoeditor/buildPlanRequest.js from sync (broke a client
rebuild), 'data/' dropped metadata_service.py, etc. The fix matches directory
patterns on path-segment boundaries. Each case below pins both directions.
"""
import pytest

from backend.services.interconnector_file_sync_service import InterconnectorFileSyncService


@pytest.fixture()
def svc():
    return InterconnectorFileSyncService()


# --- files that must NOT be excluded (the false-positives the bug dropped) ---
@pytest.mark.parametrize("path", [
    "frontend/src/components/videoeditor/buildPlanRequest.js",      # 'build/'
    "frontend/src/components/videoeditor/buildPlanRequest.test.js",
    "frontend/src/utils/smartContextBuilder.js",                   # 'build/'
    "backend/services/metadata_service.py",                        # 'data/'
    "backend/handlers/database_handler.py",                        # 'data/'
    "frontend/src/api/backupService.js",                           # 'backups/'
    "plugins/lora_trainer/scripts/setup_venv.sh",                  # 'env/' + 'venv' substring
    "scripts/dep_reconciler/detectors/torch_venv.py",             # 'env/' + 'venv' substring
    "cli/llx/commands/logs.py",                                    # 'logs/'
])
def test_source_files_are_not_excluded(svc, path):
    assert svc.should_exclude_file(path) is False, f"{path} should sync but was excluded"


# --- ALL virtualenv directories must be excluded, including suffixed sidecar
#     venvs that the literal "venv/" segment pattern misses (2026-06-15) ---
@pytest.mark.parametrize("path", [
    "backend/venv/lib/python3.12/site.py",                  # plain venv
    "plugins/video_editor/venv/lib/python3.12/os.py",       # plain venv (plugin)
    "plugins/audio_foundry/venv/bin/activate",              # plain venv (plugin)
    "plugins/lora_trainer/venv-torch/lib/torch/__init__.py",  # suffixed venv
    "plugins/audio_foundry/venv-music/bin/python",          # suffixed venv
    "some/path/.venv/lib/python3.12/site.py",               # dotted venv
    "plugins/x/venv_py311/lib/foo.py",                      # underscore-suffixed venv
])
def test_all_venv_dirs_excluded(svc, path):
    assert svc.should_exclude_file(path) is True, f"{path} is inside a venv and must be excluded"


# --- real directories that MUST still be excluded (no regression) ---
@pytest.mark.parametrize("path", [
    "frontend/dist/assets/index-abc.js",       # dist/
    "backend/venv/lib/python3.12/site.py",     # backend/venv/
    "data/training/loras/Serenity_Kane_v1.json",  # data/
    "logs/backend.log",                        # logs/ (and *.log)
    "frontend/node_modules/react/index.js",    # node_modules
    "backend/__pycache__/app.cpython-312.pyc", # __pycache__ + .pyc
    "plugins/comfyui/ComfyUI/main.py",         # multi-segment dir pattern
    "backups/old/backend/app.py",              # backups/
])
def test_real_artifacts_still_excluded(svc, path):
    assert svc.should_exclude_file(path) is True, f"{path} should be excluded but was not"
