"""Pure tests for music-video editing helpers: input hashing, restore, cleanup."""
from types import SimpleNamespace

from backend.services.music_video_service import (
    TRANSIENT_SETTING_KEYS,
    clean_settings,
    clip_input_hash,
    clip_is_stale,
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
    assert h != clip_input_hash(_clip(storyboard_path="/tmp/sb.png"), SETTINGS)


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
