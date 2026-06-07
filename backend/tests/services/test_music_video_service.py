import pytest

try:
    from flask import Flask
    from backend.models import db, MusicVideo
    import backend.services.music_video_service as mvs
    from backend.services.music_video_service import (
        MusicVideoService,
        VALID_TRANSITIONS,
        STAGE_TO_AGENT,
        compute_cut_plan,
        fill_clip_to_duration,
        MIN_CLIP_S,
    )
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


# --- State machine (cloned from Production, must stay race-safe) -------------

def test_create_initial_state(app):
    svc = MusicVideoService(db.session)
    mv = svc.create(
        name="Test", song_document_id=1, song_path="/tmp/song.mp3",
        style_prompt="deep blue, loss", project_id=None,
    )
    assert mv.status == "draft"
    assert mv.current_stage == "draft"
    assert mv.id is not None


def test_advance_rejects_when_predecessor_mismatched(app):
    svc = MusicVideoService(db.session)
    mv = svc.create(name="X", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    mv.current_stage = "complete"
    db.session.commit()
    assert svc.advance_if_predecessor(mv.id, expected_predecessor="assembling") is False


def test_advance_succeeds_and_status_tracks_stage(app):
    svc = MusicVideoService(db.session)
    mv = svc.create(name="X", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    assert svc.advance_if_predecessor(mv.id, expected_predecessor="draft") is True
    db.session.refresh(mv)
    assert mv.current_stage == "analyzing"
    assert mv.status == "analyzing"


def test_full_chain_reaches_complete(app):
    svc = MusicVideoService(db.session)
    mv = svc.create(name="X", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    for predecessor in ["draft", "analyzing", "awaiting_approval", "generating", "assembling"]:
        svc.advance_if_predecessor(mv.id, expected_predecessor=predecessor)
    db.session.refresh(mv)
    assert mv.current_stage == "complete"
    assert mv.status == "complete"


def test_fail_stage_persists_failed_status(app):
    svc = MusicVideoService(db.session)
    mv = svc.create(name="X", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    svc.fail_stage(mv.id, stage="generating", error=ValueError("boom"))
    db.session.refresh(mv)
    assert mv.status == "failed_generating"
    assert mv.error_blob["stage"] == "generating"
    assert "boom" in mv.error_blob["error"]


def test_find_non_terminal_excludes_failed_and_complete(app):
    svc = MusicVideoService(db.session)
    live = svc.create(name="live", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    svc.advance_if_predecessor(live.id, expected_predecessor="draft")  # → analyzing
    done = svc.create(name="done", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    done.status = "complete"
    failed = svc.create(name="failed", song_document_id=1, song_path=None, style_prompt="x", project_id=None)
    failed.status = "failed_generating"
    db.session.commit()
    ids = {mv.id for mv in svc.find_non_terminal()}
    assert live.id in ids
    assert done.id not in ids
    assert failed.id not in ids


def test_stage_graph_user_gates():
    # awaiting_approval is the cost gate (no auto-resume); draft is pre-pipeline.
    assert STAGE_TO_AGENT["awaiting_approval"] is None
    assert STAGE_TO_AGENT["draft"] is None
    assert STAGE_TO_AGENT["analyzing"] == "analyzer"
    assert STAGE_TO_AGENT["generating"] == "clip_generator"
    assert STAGE_TO_AGENT["assembling"] == "assembler"
    assert VALID_TRANSITIONS["assembling"] == "complete"


# --- Cut cadence (pure) ------------------------------------------------------

def _even_beats(n, step=0.5):
    return [round(i * step, 3) for i in range(1, n + 1)]


def test_cadence_high_energy_cuts_more_than_low():
    beats = _even_beats(64, step=0.5)  # 0.5..32.0s, 0.5s spacing
    duration = 33.0
    # Two sections: loud first half, quiet second half.
    sections = [
        {"label": "drop", "start": 0.0, "end": 16.0, "mean_energy": 1.0},
        {"label": "outro", "start": 16.0, "end": duration, "mean_energy": 0.0},
    ]
    plan = compute_cut_plan(beats, sections, duration)
    loud = [c for c in plan if c["start_s"] < 16.0]
    quiet = [c for c in plan if c["start_s"] >= 16.0]
    # High-energy section should produce strictly more cuts than the low one.
    assert len(loud) > len(quiet)


def test_cadence_respects_min_clip_floor():
    beats = _even_beats(40, step=0.2)  # fast 0.2s spacing → would violate floor at K=1
    duration = 8.5
    sections = [{"label": "drop", "start": 0.0, "end": duration, "mean_energy": 1.0}]
    plan = compute_cut_plan(beats, sections, duration)
    # Every cut except possibly the snapped tail must clear MIN_CLIP_S.
    for c in plan:
        assert c["end_s"] - c["start_s"] >= MIN_CLIP_S - 1e-9


def test_cadence_full_coverage_and_coterminate():
    beats = _even_beats(50, step=0.5)
    duration = 26.0
    sections = [
        {"label": "intro", "start": 0.0, "end": 13.0, "mean_energy": 0.2},
        {"label": "drop", "start": 13.0, "end": duration, "mean_energy": 0.9},
    ]
    plan = compute_cut_plan(beats, sections, duration)
    assert plan[0]["start_s"] == 0.0
    assert plan[-1]["end_s"] == pytest.approx(duration)
    # Contiguous, no gaps/overlaps.
    for a, b in zip(plan, plan[1:]):
        assert b["start_s"] == pytest.approx(a["end_s"])
    # Indices are 0..n-1.
    assert [c["index"] for c in plan] == list(range(len(plan)))


def test_cadence_no_beats_degenerate_single_cut():
    plan = compute_cut_plan([], [{"label": "x", "start": 0.0, "end": 10.0, "mean_energy": 1.0}], 10.0)
    assert len(plan) == 1
    assert plan[0]["start_s"] == 0.0 and plan[0]["end_s"] == 10.0


def test_cadence_flat_energy_still_produces_plan():
    # All sections equal energy (the real-song case ~4% spread → emax==emin guard).
    beats = _even_beats(30, step=1.0)
    duration = 31.0
    sections = [
        {"label": "a", "start": 0.0, "end": 15.5, "mean_energy": 1.0},
        {"label": "b", "start": 15.5, "end": duration, "mean_energy": 1.0},
    ]
    plan = compute_cut_plan(beats, sections, duration)
    assert len(plan) >= 2
    assert plan[-1]["end_s"] == pytest.approx(duration)


# --- Cut-plan cap: planner sizes slots to clip capability (no reverse needed) -

_CAP_SECTIONS = [
    {"label": "drop", "start": 0.0, "end": 16.0, "mean_energy": 1.0},
    {"label": "outro", "start": 16.0, "end": 33.0, "mean_energy": 0.0},  # long low-energy holds
]


def test_cut_plan_caps_long_cuts_with_max_cut_s():
    beats = _even_beats(64, step=0.5)
    duration = 33.0
    uncapped = compute_cut_plan(beats, _CAP_SECTIONS, duration)
    capped = compute_cut_plan(beats, _CAP_SECTIONS, duration, max_cut_s=2.0)
    # Every slot now fits what a forward clip can fill.
    for c in capped:
        assert c["end_s"] - c["start_s"] <= 2.0 + 1e-6
    # The cap split at least one long low-energy hold into more (shorter) cuts.
    assert len(capped) > len(uncapped)
    # Still contiguous, coterminal, and index-contiguous.
    assert capped[0]["start_s"] == 0.0
    assert capped[-1]["end_s"] == pytest.approx(duration)
    for a, b in zip(capped, capped[1:]):
        assert b["start_s"] == pytest.approx(a["end_s"])
    assert [c["index"] for c in capped] == list(range(len(capped)))


def test_cut_plan_max_cut_s_none_is_unchanged():
    beats = _even_beats(64, step=0.5)
    duration = 33.0
    assert compute_cut_plan(beats, _CAP_SECTIONS, duration) == \
        compute_cut_plan(beats, _CAP_SECTIONS, duration, max_cut_s=None)


def test_cut_plan_cap_splits_degenerate_no_beats():
    # No beats → one whole-song cut; the cap still splits it into forward sub-cuts.
    plan = compute_cut_plan([], [{"label": "x", "start": 0.0, "end": 9.0, "mean_energy": 1.0}],
                            9.0, max_cut_s=2.0)
    assert len(plan) >= 5
    for c in plan:
        assert c["end_s"] - c["start_s"] <= 2.0 + 1e-6
    assert plan[-1]["end_s"] == pytest.approx(9.0)


# --- fill_clip_to_duration: the moonwalk fix (forward, never reversed) --------

def _capture_fill(monkeypatch, *, target_s, src_len=2.0, method="forward", max_stretch=2.0):
    """Build the ffmpeg invocation WITHOUT running it; return (cmd, filtergraph)."""
    monkeypatch.setattr(mvs, "probe_duration", lambda *a, **k: src_len)

    class _Ok:
        returncode = 0
        stderr = ""

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Ok()
    monkeypatch.setattr(mvs.subprocess, "run", fake_run)
    fill_clip_to_duration("/tmp/in.mp4", target_s, "/tmp/out.mp4",
                          method=method, max_stretch=max_stretch)
    cmd = captured["cmd"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    return cmd, graph


def test_fill_forward_never_reverses(monkeypatch):
    # The moonwalk regression guard: forward fill must NOT reverse or boomerang.
    cmd, graph = _capture_fill(monkeypatch, target_s=3.0, src_len=2.0, method="forward")
    assert "reverse" not in graph
    assert "concat=n=2" not in graph
    assert "setpts" in graph                      # forward slowdown present
    assert "-stream_loop" not in cmd
    assert cmd[-1] == "/tmp/out.mp4"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "3.0000"   # exact-length trim


def test_fill_forward_slowdown_factor(monkeypatch):
    # 3.0s slot from a 2.0s clip → 1.5× forward slowdown, no last-frame hold.
    _, graph = _capture_fill(monkeypatch, target_s=3.0, src_len=2.0, max_stretch=2.0)
    assert "setpts=1.5000*PTS" in graph
    assert "tpad" not in graph


def test_fill_forward_holds_last_frame_past_stretch_cap(monkeypatch):
    # 8.0s slot from a 2.0s clip with cap 2× → slow to 2×, then HOLD (never reverse).
    _, graph = _capture_fill(monkeypatch, target_s=8.0, src_len=2.0, max_stretch=2.0)
    assert "reverse" not in graph
    assert "setpts=2.0000*PTS" in graph
    assert "tpad=stop_mode=clone" in graph


def test_fill_boomerang_still_available_opt_in(monkeypatch):
    # Boomerang is kept as an explicit choice — and it DOES reverse (positive case
    # paired with the forward negative case = a real, non-placebo assertion).
    _, graph = _capture_fill(monkeypatch, target_s=6.0, src_len=2.0, method="boomerang")
    assert "reverse" in graph
    assert "concat=n=2" in graph


def test_fill_loop_uses_stream_loop_and_no_reverse(monkeypatch):
    cmd, graph = _capture_fill(monkeypatch, target_s=6.0, src_len=2.0, method="loop")
    assert "-stream_loop" in cmd
    assert "reverse" not in graph
