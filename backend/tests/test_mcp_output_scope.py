"""Which files under data/outputs MCP clients can list and read.

Builds its own outputs tree in a temp folder; no backend, GPU or network.
"""

import asyncio
import json
import os
import sys

import pytest


def _import_mcp_sdk():
    """Import the installed `mcp` SDK, not backend/mcp, which shadows it when
    backend/ is on sys.path (backend/tests/conftest.py puts it there)."""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    loaded = sys.modules.get("mcp")
    if loaded is not None and (getattr(loaded, "__file__", "") or "").startswith(backend_dir + os.sep):
        for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")]:
            del sys.modules[name]
    saved = list(sys.path)
    try:
        sys.path = [p for p in sys.path if os.path.abspath(p or ".") != backend_dir]
        return pytest.importorskip("mcp.types")
    finally:
        sys.path = saved


_import_mcp_sdk()

from backend.mcp import config as mcp_config  # noqa: E402
from backend.mcp import resources_adapter as ra  # noqa: E402
from backend.mcp.config import MCPConfig  # noqa: E402

SERVED = [
    "generated_images/cat.png",
    "generated_animations/loop.gif",
    "videos/editor-renders/cut.mp4",
    "audio/song.wav",
    "narrations/intro.wav",
    "storyboards/1/shot_1.png",
    "character_samples/2/sample_0.png",
    "csv/table.csv",
    "files/notes.md",
    "code/app.py",
    "upscaling/output/images/big.png",
    "bulk_rows.csv",
]
HIDDEN = [
    "chat-exports/chats-20260101-000000/index.json",
    "chat-exports/chats-20260101-000000/sessions/s1.md",
    "screenshots/agent_capture_1.webp",
    "consent/abc123.consent",
    "training/demo/s00.wav",
    "edit_inputs/edit_src_1.png",
    "upscaling/input/holiday.mp4",
    "tracking/job_tracking_1.json",
    "demos/EP01.mp4",
    "swarm_plans/plan.md",
    "generated_images/cat.png.consent",
    "audio/.jobs/job1.json",
    ".progress_jobs/job.json",
]


@pytest.fixture
def outputs(tmp_path):
    root = tmp_path.resolve() / "outputs"
    for rel in SERVED + HIDDEN:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


def _default_scope():
    return ra.OutputScope.from_policy(MCPConfig().resources)


def _listed(root, scope):
    seen, cursor = [], None
    while True:
        page, cursor = ra._list_page(root, scope, cursor, page_size=3)
        seen += [p.relative_to(root).as_posix() for p in page]
        if cursor is None:
            return seen


def test_default_listing_is_generated_media_and_root_files_only(outputs):
    assert sorted(_listed(outputs, _default_scope())) == sorted(SERVED)


@pytest.mark.parametrize("rel", HIDDEN)
def test_a_hidden_file_cannot_be_read_by_uri(outputs, rel):
    with pytest.raises(FileNotFoundError):
        ra._read_contents(ra._uri_for(outputs / rel, outputs), outputs, _default_scope(), 1024)


@pytest.mark.parametrize("rel", SERVED)
def test_every_listed_file_reads(outputs, rel):
    _contents, size = ra._read_contents(ra._uri_for(outputs / rel, outputs), outputs, _default_scope(), 1024)
    assert size == 1


@pytest.mark.parametrize("uri", [
    "guaardvark://outputs/generated_images/../chat-exports/chats-20260101-000000/index.json",
    "guaardvark://outputs/generated_images/..%2Fchat-exports%2Fchats-20260101-000000%2Findex.json",
    "guaardvark://outputs/generated_images/%2e%2e/consent/abc123.consent",
    "guaardvark://outputs/Chat-Exports/chats-20260101-000000/index.json",
    "guaardvark://outputs/",
])
def test_a_dotted_encoded_or_recased_uri_does_not_reach_a_hidden_folder(outputs, uri):
    with pytest.raises(FileNotFoundError):
        ra._read_contents(uri, outputs, _default_scope(), 1024)


def test_a_symlink_in_a_served_folder_into_a_hidden_one_is_refused(outputs):
    link = outputs / "generated_images" / "looks_like_media.json"
    os.symlink(outputs / "chat-exports" / "chats-20260101-000000" / "index.json", link)
    assert "generated_images/looks_like_media.json" not in _listed(outputs, _default_scope())
    with pytest.raises(FileNotFoundError):
        ra._read_contents(ra._uri_for(link, outputs), outputs, _default_scope(), 1024)


def test_a_folder_named_outputs_lists_and_reads_the_same_file(outputs):
    (outputs / "a.txt").write_text("root")
    (outputs / "outputs").mkdir()
    (outputs / "outputs" / "a.txt").write_text("nested")
    scope = ra.OutputScope(folders=(("outputs",),), root_files=True)
    for rel in ("outputs/a.txt", "a.txt"):
        uri = ra._uri_for(outputs / rel, outputs)
        assert ra._path_for_uri(uri, outputs) == outputs / rel
        assert rel in _listed(outputs, scope)
    contents, _size = ra._read_contents("guaardvark://outputs/outputs/a.txt", outputs, scope, 1024)
    assert contents.text == "nested"


def test_root_files_can_be_switched_off(outputs):
    scope = ra.OutputScope.from_policy(
        mcp_config.ResourcePolicy(outputs_folders=["csv"], outputs_root_files=False)
    )
    assert _listed(outputs, scope) == ["csv/table.csv"]


def test_folder_entries_that_climb_are_ignored():
    scope = ra.OutputScope.from_policy(
        mcp_config.ResourcePolicy(outputs_folders=["../etc", "./x", "ok/sub", ""])
    )
    assert scope.folders == (("ok", "sub"),)


def test_mcp_json_sets_the_folder_list(tmp_path, monkeypatch):
    cfg_file = tmp_path / "mcp.json"
    cfg_file.write_text(json.dumps({"server": {"resources": {
        "outputs_folders": ["generated_images"], "outputs_root_files": False,
    }}}))
    monkeypatch.setattr(mcp_config, "_config_path", lambda: cfg_file)
    cfg = mcp_config.load_config()
    assert cfg.resources.outputs_folders == ["generated_images"]
    assert cfg.resources.outputs_root_files is False


def test_count_is_full_while_the_banner_count_is_capped(tmp_path):
    root = tmp_path.resolve() / "outputs"
    (root / "generated_images").mkdir(parents=True)
    for i in range(ra._BANNER_COUNT_LIMIT + 10):
        (root / "generated_images" / f"{i:04d}.png").write_bytes(b"x")
    (root / "chat-exports").mkdir()
    (root / "chat-exports" / "index.json").write_text("{}")
    cfg = MCPConfig()
    cfg.resources.outputs_root = str(root)

    assert ra.count_resources(cfg) == ra._BANNER_COUNT_LIMIT + 10
    _on_list, _on_read, banner = ra.build_resource_handlers(cfg)
    assert banner == ra._BANNER_COUNT_LIMIT


def test_the_handlers_apply_the_configured_scope(outputs):
    cfg = MCPConfig()
    cfg.resources.outputs_root = str(outputs)
    on_list, on_read, _count = ra.build_resource_handlers(cfg)

    async def run():
        names, cursor = [], None
        while True:
            page = await on_list(None, ra.mcp_types.PaginatedRequestParams(cursor=cursor))
            names += [r.uri[len(ra.URI_PREFIX):] for r in page.resources]
            cursor = page.next_cursor
            if cursor is None:
                break
        with pytest.raises(FileNotFoundError):
            await on_read(None, ra.mcp_types.ReadResourceRequestParams(
                uri="guaardvark://outputs/chat-exports/chats-20260101-000000/index.json"))
        return names

    assert sorted(asyncio.run(run())) == sorted(SERVED)
