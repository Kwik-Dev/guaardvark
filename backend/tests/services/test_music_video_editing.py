"""Pure tests for music-video editing helpers: input hashing, restore, cleanup."""
from types import SimpleNamespace

import os
import shutil

from backend.services.music_video_service import (
    TRANSIENT_SETTING_KEYS,
    clean_settings,
    clip_file_name,
    clip_input_hash,
    clip_is_stale,
    clip_needs_render,
    is_marked_video_output,
    mark_video_output,
    prune_unused_clip_files,
    restored_clips,
    snapshot_of,
)

SETTINGS = {"i2v_model": "wan22-14b-i2v", "max_stretch": 2.0, "notes": "x"}


def _clip(**kw):
    c = {"index": 0, "start": 0.0, "end": 2.5, "prompt": "a red door", "status": "done",
         "clip_path": "/tmp/clip_0.mp4"}
    c.update(kw)
    return c


def test_hash_is_stable_and_ignores_unrelated_settings():
    c = _clip()
    h = clip_input_hash(c, SETTINGS)
    assert h == clip_input_hash(dict(c), dict(SETTINGS))
    assert h == clip_input_hash(c, {**SETTINGS, "notes": "changed", "progress": {"stage": "x"},
                                    "stage_timings": {}})


def test_hash_changes_with_prompt_seed_and_render_settings():
    c = _clip()
    h = clip_input_hash(c, SETTINGS)
    assert h != clip_input_hash(_clip(prompt="a blue door"), SETTINGS)
    assert h != clip_input_hash(_clip(seed=7), SETTINGS)
    assert h != clip_input_hash(c, {**SETTINGS, "i2v_model": "cogvideox-5b-i2v"})
    assert h != clip_input_hash(c, {**SETTINGS, "flux_unet": "other.safetensors"})
    assert h != clip_input_hash(_clip(rerenders=1), SETTINGS)


def test_storyboard_counts_by_its_file(tmp_path):
    sb = tmp_path / "storyboard_0.png"
    sb.write_bytes(b"first")
    with_sb = _clip(storyboard_path=str(sb))
    first = clip_input_hash(with_sb, SETTINGS)
    assert first != clip_input_hash(_clip(), SETTINGS)
    # Regenerated in place: the clip animated from the old picture is stale.
    sb.write_bytes(b"second, longer")
    os.utime(sb, ns=(1, 1))
    second = clip_input_hash(with_sb, SETTINGS)
    assert second != first
    # A copy that keeps the timestamp (a duplicated video) matches.
    copy = tmp_path / "copy" / "storyboard_0.png"
    copy.parent.mkdir()
    shutil.copy2(sb, copy)
    assert clip_input_hash(_clip(storyboard_path=str(copy)), SETTINGS) == second


def test_a_cut_without_a_prompt_hashes_the_style_it_renders_from():
    bare = _clip(prompt="")
    assert clip_input_hash(bare, SETTINGS, "neon city") != clip_input_hash(bare, SETTINGS, "forest")
    assert clip_input_hash(_clip(), SETTINGS, "neon city") == clip_input_hash(_clip(), SETTINGS, "forest")


def test_needs_render():
    on_disk = lambda p: p == "/tmp/clip_0.mp4"  # noqa: E731
    done = _clip()
    done["input_hash"] = clip_input_hash(done, SETTINGS)
    assert not clip_needs_render(done, SETTINGS, exists=on_disk)
    assert clip_needs_render({**done, "prompt": "changed"}, SETTINGS, exists=on_disk)
    assert clip_needs_render({**done, "clip_path": "/gone.mp4"}, SETTINGS, exists=on_disk)
    assert clip_needs_render(_clip(status="pending"), SETTINGS, exists=on_disk)
    assert not clip_needs_render(_clip(status="cancelled"), SETTINGS, exists=on_disk)


def test_each_set_of_inputs_renders_to_its_own_file():
    a, b = clip_input_hash(_clip(), SETTINGS), clip_input_hash(_clip(seed=3), SETTINGS)
    assert clip_file_name(0, a) != clip_file_name(0, b)
    assert clip_file_name(0, a) == f"clip_0_{a}.mp4"


def test_pruning_keeps_what_a_version_points_at_and_foreign_files(tmp_path):
    names = ["clip_0_" + "a" * 16 + ".mp4", "clip_0_" + "b" * 16 + ".mp4",
             "clip_0.mp4", "clip_1_" + "c" * 16 + ".mp4", "notes.txt"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    kept = str(tmp_path / names[0])
    removed = prune_unused_clip_files(tmp_path, 0, {kept})
    assert removed == [str(tmp_path / names[1])]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(n for n in names if n != names[1])


def test_video_output_mark_survives_other_metadata():
    doc = SimpleNamespace(file_metadata='{"source": "editor"}')
    assert not is_marked_video_output(doc)
    mark_video_output(doc, "music_video", 7)
    assert is_marked_video_output(doc)
    assert '"source": "editor"' in doc.file_metadata
    bad = SimpleNamespace(file_metadata="not json")
    mark_video_output(bad, "music_video", 7)
    assert is_marked_video_output(bad)


def test_stale_only_when_recorded_hash_differs():
    c = _clip()
    assert not clip_is_stale(c, SETTINGS)                      # legacy clip: no hash
    c["input_hash"] = clip_input_hash(c, SETTINGS)
    assert not clip_is_stale(c, SETTINGS)
    c["prompt"] = "something else"
    assert clip_is_stale(c, SETTINGS)


def test_restored_clips_reset_missing_files_failed_and_cancelled():
    clips = [
        _clip(index=0, clip_path="/keep.mp4"),
        _clip(index=1, clip_path="/gone.mp4", review={"state": "approved"}),
        _clip(index=2, status="failed"),
        _clip(index=3, status="cancelled", deferred_since=1.0),
    ]
    out = restored_clips(clips, exists=lambda p: p == "/keep.mp4")
    assert out[0]["status"] == "done"
    assert out[1]["status"] == "pending" and out[1]["clip_path"] is None and out[1]["review"] is None
    assert out[2]["status"] == "pending"
    assert out[3]["status"] == "pending" and "deferred_since" not in out[3]
    assert clips[1]["status"] == "done"   # input not mutated


def test_clean_settings_drops_run_time_keys():
    s = {k: 1 for k in TRANSIENT_SETTING_KEYS}
    s["i2v_model"] = "m"
    assert clean_settings(s) == {"i2v_model": "m"}


def test_snapshot_is_a_deep_copy():
    mv = SimpleNamespace(name="N", style_prompt="S", project_id=3, cut_plan=[{"index": 0}],
                         clips=[_clip()], settings_json={"progress": {}, "i2v_model": "m"},
                         output_document_id=9, current_stage="complete")
    snap = snapshot_of(mv)
    snap["clips"][0]["prompt"] = "mutated"
    assert mv.clips[0]["prompt"] == "a red door"
    assert snap["settings"] == {"i2v_model": "m"}
    assert snap["project_id"] == 3 and snap["output_document_id"] == 9
