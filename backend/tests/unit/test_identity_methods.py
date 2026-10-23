"""The Cast identity check reports a number only when it has earned one.

The method these replace divided the smoke PNG's byte count by the mean
reference byte count and showed the result as an identity score, green above
0.75. These tests pin the things that stop that returning: the smoke path
cannot reach "size", a method without a measured row for the model it used
cannot show a score, a model that fails or will not answer cannot produce a
match, and the encoder is read from its verified install folder or not at all.

Every model call is mocked, and the encoder is a stand-in
(backend/tests/fixtures/identity_encoder_stub.py) laid out under tmp_path.
Nothing here loads weights, reaches Ollama, or touches the network.
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from backend.services import identity_encoder
from backend.services import identity_method_data as data
from backend.services import video_consistency_metrics as vcm
from backend.tests.fixtures import identity_encoder_stub as stub


@pytest.fixture
def images(tmp_path):
    """One reference and one candidate PNG, deliberately different file sizes.

    The candidate is flat colour and the reference is noise, so a byte-count
    method would score them far apart — if a file-size score ever returns, the
    smoke-path test below sees it.
    """
    ref = tmp_path / "ref.png"
    cand = tmp_path / "cand.png"
    Image.frombytes("RGB", (64, 64), bytes(range(256)) * 48).save(ref)
    Image.new("RGB", (64, 64), (10, 120, 200)).save(cand)
    return str(ref), str(cand)


class _Reply:
    """What VisionAnalyzer.analyze returns, as score_identity_preservation reads it."""

    def __init__(self, description="", success=True, error=None, model_used="vlm-test:latest"):
        self.description = description
        self.success = success
        self.error = error
        self.model_used = model_used


class _Analyzer:
    """Stand-in VisionAnalyzer. Records the prompts it was asked, answers from a list."""

    default_model = "vlm-test:latest"

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []

    def analyze(self, image, prompt, **kwargs):
        self.prompts.append(prompt)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def proven_vlm(monkeypatch):
    """A measured row for the stand-in vision model that clears the floor."""
    monkeypatch.setitem(
        data.IDENTITY_METHODS["vlm"], "measured",
        [{"model": "vlm-test:latest", "digest": "deadbeef", "correct": 60, "n": 60,
          "date": "2026-10-06"}],
    )


# ── The smoke path never returns a file-size score ───────────────────────────

def test_smoke_path_returns_no_score_and_no_size_method(images):
    """No encoder installed: no number, and the reason names where to install it."""
    ref, cand = images
    identity = vcm.score_smoke_identity([ref], cand)

    assert identity["status"] == "not_measured"
    assert identity["score"] is None
    assert identity["method"] is None
    assert identity["reason"] == f"embed: {vcm.ENCODER_NOT_INSTALLED}"


def test_smoke_path_does_not_consult_the_size_method(images):
    """Only a method with a measured row is tried — embed, not vlm — and never size."""
    ref, cand = images
    with patch.object(
        vcm, "score_identity_preservation", wraps=vcm.score_identity_preservation
    ) as scorer:
        vcm.score_smoke_vs_refs([ref], cand)
    assert [c.kwargs.get("method") for c in scorer.call_args_list] == ["embed"]


def test_score_smoke_vs_refs_keeps_its_stats_and_identity_shape(images):
    ref, cand = images
    out = vcm.score_smoke_vs_refs([ref], cand)
    assert out["stats"]["exists"] is True
    assert out["identity"]["score"] is None


def test_size_method_is_still_there_for_other_callers(images):
    """Kept deliberately: it is not an identity signal, but removing it would
    change what batch_video_generator's hist fallback lands on."""
    ref, cand = images
    result = vcm.score_identity_preservation([ref], cand, method="size")
    assert result["method"] == "size"
    assert result["score"] is not None


# ── The vlm parser ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply,expected", [
    ("yes", True),
    ("no", False),
    ("Yes", True),
    ("NO.", False),
    ("Yes, same person", True),
    ("No — different hairline and jaw", False),
    ("**yes**", True),
    ('{"same_subject": true}', True),
    ('{"same_subject": false}', False),
    ("true", True),
    ("false", False),
    ("garbage", None),
    ("", None),
    (None, None),
    ("Nothing in the second image resembles the first", None),
    ("I cannot tell from these images", None),
    ("maybe", None),
    ("1", None),
    ('{"same": true, "confident": false}', None),
])
def test_parse_same_subject(reply, expected):
    assert vcm.parse_same_subject(reply) is expected


# ── vlm end to end, mocked ───────────────────────────────────────────────────

def test_vlm_reports_the_fraction_of_yes_over_up_to_three_refs(images, proven_vlm):
    ref, cand = images
    analyzer = _Analyzer([_Reply("yes"), _Reply("no"), _Reply("Yes, same character")])
    result = vcm.score_identity_preservation(
        [ref, ref, ref, ref], cand, method="vlm", analyzer=analyzer
    )

    assert len(analyzer.prompts) == 3, "compares against at most three references"
    assert result["status"] == "measured"
    assert result["score"] == pytest.approx(2 / 3, abs=1e-3)
    assert result["details"]["refs_compared"] == 3
    assert "same person or character" in analyzer.prompts[0]


@pytest.mark.parametrize("replies,yes,match", [
    (["yes", "yes", "yes"], 3, True),
    (["yes", "no", "yes"], 2, True),
    (["no", "yes", "no"], 1, False),
    (["no", "no", "no"], 0, False),
])
def test_two_of_three_references_is_a_match_and_one_is_not(
    images, proven_vlm, replies, yes, match
):
    """The only scores this method can produce are 0, 1/3, 2/3 and 1, so the
    threshold has to sit just below 2/3 — at 0.67 a 2-of-3 majority failed."""
    ref, cand = images
    analyzer = _Analyzer([_Reply(r) for r in replies])
    result = vcm.score_identity_preservation(
        [ref] * 3, cand, method="vlm", analyzer=analyzer
    )

    assert result["details"]["yes"] == yes
    assert result["match"] is match


def test_vision_error_is_not_measured_and_not_a_match(images, proven_vlm):
    ref, cand = images
    analyzer = _Analyzer([_Reply(success=False, error="Ollama timed out after 45s")])
    result = vcm.score_identity_preservation([ref], cand, method="vlm", analyzer=analyzer)

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert "could not answer" in result["reason"]
    assert "match" not in result


def test_vision_exception_is_not_measured(images, proven_vlm):
    ref, cand = images
    analyzer = _Analyzer([RuntimeError("connection refused")])
    result = vcm.score_identity_preservation([ref], cand, method="vlm", analyzer=analyzer)

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert "could not be reached" in result["reason"]


def test_unparseable_reply_is_not_measured_even_among_yeses(images, proven_vlm):
    """Fail closed: one reply that is not yes/no ends the run with no score,
    rather than scoring the pairs that happened to answer."""
    ref, cand = images
    analyzer = _Analyzer([_Reply("yes"), _Reply("it is hard to say")])
    result = vcm.score_identity_preservation(
        [ref, ref], cand, method="vlm", analyzer=analyzer
    )

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["reason"] == "the vision model did not answer yes or no"


def test_vlm_all_yes_is_still_withheld_without_a_measured_row(images):
    ref, cand = images
    analyzer = _Analyzer([_Reply("yes"), _Reply("yes"), _Reply("yes")])
    result = vcm.score_identity_preservation(
        [ref, ref, ref], cand, method="vlm", analyzer=analyzer
    )

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["reason"] == data.NOT_PROVEN_REASON
    assert result["details"]["raw_score"] == 1.0


# ── embed: read from the verified install folder, or not at all ──────────────

class _Loader:
    """A from_pretrained that records how it was called and reports no local copy."""

    def __init__(self):
        self.calls = []

    def from_pretrained(self, name_or_path, **kwargs):
        self.calls.append((name_or_path, kwargs))
        raise OSError(f"{name_or_path} does not appear to have a file named config.json")


@pytest.fixture(autouse=True)
def _clear_encoder_cache():
    vcm._ENCODER_CACHE.clear()
    yield
    vcm._ENCODER_CACHE.clear()


@pytest.fixture
def install_root(tmp_path, monkeypatch):
    """An empty install root of this test's own; stub.fake_install fills it."""
    root = tmp_path / "identity_encoder"
    monkeypatch.setattr(identity_encoder, "install_root", lambda: root)
    return root


def test_embed_not_installed_loads_nothing(images, install_root):
    ref, cand = images
    processor, model = _Loader(), _Loader()
    with patch.object(vcm, "_image_encoder_classes", return_value=(processor, model)):
        result = vcm.score_identity_preservation([ref], cand, method="embed")

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["reason"] == vcm.ENCODER_NOT_INSTALLED
    assert processor.calls == [] and model.calls == [], "nothing is loaded without an install"


@pytest.mark.parametrize("record", [
    {"revision": "0" * 40},  # an install of another commit
    {"sha256": "0" * 64},    # weights that did not match the pin
])
def test_an_install_of_other_weights_is_not_installed(images, install_root, record):
    stub.fake_install(install_root, **record)
    assert identity_encoder.installed_path() is None
    ref, cand = images
    result = vcm.score_identity_preservation([ref], cand, method="embed")
    assert result["reason"] == vcm.ENCODER_NOT_INSTALLED


def test_embed_reads_the_install_folder_with_local_files_only(images, install_root):
    folder = stub.fake_install(install_root)
    ref, cand = images
    processor, model = _Loader(), _Loader()
    with patch.object(vcm, "_image_encoder_classes", return_value=(processor, model)):
        result = vcm.score_identity_preservation([ref], cand, method="embed")

    assert result["status"] == "not_measured"
    assert result["reason"] == vcm.ENCODER_NOT_INSTALLED
    assert processor.calls, "the encoder load was attempted"
    for name, kwargs in processor.calls + model.calls:
        assert name == str(folder), "loaded from the install folder, not by hub id"
        assert kwargs.get("local_files_only") is True
    assert not vcm._ENCODER_CACHE, "a failed load is not cached"


def _coloured(tmp_path, name, rgb):
    path = tmp_path / name
    Image.new("RGB", (32, 32), rgb).save(path)
    return str(path)


def test_installed_and_proven_embed_scores_against_the_measured_threshold(tmp_path, install_root):
    """The shipped row proves the pinned encoder, so a score comes back with
    the declared threshold; same picture matches, a different one does not."""
    stub.fake_install(install_root)
    red = _coloured(tmp_path, "red.png", (230, 20, 20))
    red2 = _coloured(tmp_path, "red2.png", (225, 25, 15))
    blue = _coloured(tmp_path, "blue.png", (20, 20, 230))
    with patch.object(vcm, "_image_encoder_classes", return_value=stub.stub_classes()):
        same = vcm.score_smoke_identity([red, red2], red)
        other = vcm.score_smoke_identity([red, red2], blue)

    threshold = data.IDENTITY_METHODS["embed"]["threshold"]
    assert same["status"] == "measured" and same["method"] == "embed"
    assert same["threshold"] == threshold and same["match"] is True
    assert other["status"] == "measured" and other["match"] is False
    assert other["score"] < threshold <= same["score"]


def test_remove_drops_the_loaded_encoder(tmp_path, install_root):
    """A copy loaded before Remove must not keep scoring after it."""
    folder = stub.fake_install(install_root)
    red = _coloured(tmp_path, "red.png", (230, 20, 20))
    with patch.object(vcm, "_image_encoder_classes", return_value=stub.stub_classes()):
        assert vcm.score_smoke_identity([red], red)["status"] == "measured"
        assert vcm._ENCODER_CACHE
        identity_encoder.remove()
        after = vcm.score_smoke_identity([red], red)
    assert not folder.exists()
    assert after["score"] is None and vcm.ENCODER_NOT_INSTALLED in after["reason"]
    assert not vcm._ENCODER_CACHE


def test_embed_never_reaches_the_hub_or_a_download_path(images, install_root):
    """The identity check is not allowed to be the thing that starts a download."""
    ref, cand = images
    processor, model = _Loader(), _Loader()
    with patch("backend.services.local_weights.is_cached") as cached, \
            patch.object(vcm, "_image_encoder_classes", return_value=(processor, model)):
        result = vcm.score_identity_preservation([ref], cand, method="embed")

    assert result["status"] == "not_measured"
    assert cached.call_args_list == [], "no cache or hub probe on this path"


# ── The evidence gate ────────────────────────────────────────────────────────

def test_every_method_declares_a_floor_and_a_threshold():
    for spec in data.IDENTITY_METHODS.values():
        assert spec["floor"] >= data.DEFAULT_IDENTITY_FLOOR
        assert 0.0 < spec["threshold"] < 1.0


def test_vlm_ships_without_evidence():
    """Its model is whichever vision model the machine has; no shipped row can be for it."""
    assert data.IDENTITY_METHODS["vlm"]["measured"] == []


def test_every_shipped_embed_row_says_what_it_was_measured_against():
    spec = data.IDENTITY_METHODS["embed"]
    entry = data.IMAGE_ENCODERS[spec["encoder"]]
    assert spec["measured"], "the embed pass mark is measured"
    for row in spec["measured"]:
        assert row["model"] == spec["encoder"]
        assert entry["weights_sha256"].startswith(row["digest"]) and len(row["digest"]) >= 12
        assert row["revision"] == entry["revision"]
        assert row["threshold"] == spec["threshold"]
        assert 0 < row["correct"] <= row["n"]
        assert row["date"] and row["set"]


def test_the_shipped_row_proves_only_the_pinned_weights():
    spec = data.IDENTITY_METHODS["embed"]
    pinned = data.IMAGE_ENCODERS[spec["encoder"]]["weights_sha256"]
    assert data.proven_for_model("embed", spec["encoder"], digest=pinned) is True
    assert data.proven_for_model("embed", spec["encoder"], digest="f" * 64) is False


def test_a_row_scored_at_another_pass_mark_is_not_proof(monkeypatch):
    row = dict(data.IDENTITY_METHODS["embed"]["measured"][0])
    monkeypatch.setitem(data.IDENTITY_METHODS["embed"], "threshold", row["threshold"] + 0.1)
    assert data.proven_for_model("embed", row["model"]) is False


def test_empty_measured_list_is_not_proven(monkeypatch):
    monkeypatch.setitem(data.IDENTITY_METHODS["embed"], "measured", [])
    assert data.proven_for_model("vlm", "vlm-test:latest") is False
    assert data.proven_for_model("embed", "facebook/dinov2-base") is False


def test_a_row_that_clears_the_lower_bound_is_proven(monkeypatch):
    monkeypatch.setitem(
        data.IDENTITY_METHODS["embed"], "measured",
        [{"model": "facebook/dinov2-base", "digest": "abc123", "correct": 96, "n": 100,
          "date": "2026-10-06"}],
    )
    assert data.accuracy_lower_bound(96, 100) >= data.IDENTITY_METHODS["embed"]["floor"]
    assert data.proven_for_model("embed", "facebook/dinov2-base") is True


def test_a_lucky_short_run_does_not_clear_the_floor(monkeypatch):
    """9 of 9 is 1.0 observed and 0.74 at the bound — not proof of 0.9."""
    monkeypatch.setitem(
        data.IDENTITY_METHODS["embed"], "measured",
        [{"model": "facebook/dinov2-base", "digest": "abc123", "correct": 9, "n": 9,
          "date": "2026-10-06"}],
    )
    assert data.accuracy_lower_bound(9, 9) < data.IDENTITY_METHODS["embed"]["floor"]
    assert data.proven_for_model("embed", "facebook/dinov2-base") is False


def test_a_row_for_another_model_does_not_transfer(monkeypatch):
    monkeypatch.setitem(
        data.IDENTITY_METHODS["vlm"], "measured",
        [{"model": "some-other-vlm:9b", "digest": "abc123", "correct": 96, "n": 100,
          "date": "2026-10-06"}],
    )
    assert data.proven_for_model("vlm", "vlm-test:latest") is False


def test_scipy_missing_withholds_the_score_rather_than_granting_it(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "scipy.stats", None)
    assert data.accuracy_lower_bound(100, 100) == 0.0


def test_a_measured_method_is_used_by_the_smoke_path(images, proven_vlm, monkeypatch):
    """With evidence in place the score comes back, with its own threshold."""
    ref, cand = images
    analyzer = _Analyzer([_Reply("yes"), _Reply("yes")])
    monkeypatch.setitem(data.IDENTITY_METHODS["vlm"], "refs_compared", 2)
    identity = vcm.score_smoke_identity([ref, ref], cand, analyzer=analyzer)

    assert identity["status"] == "measured"
    assert identity["method"] == "vlm"
    assert identity["score"] == 1.0
    assert identity["threshold"] == data.IDENTITY_METHODS["vlm"]["threshold"]
    assert identity["match"] is True


def test_the_prompt_asks_the_closed_question_verbatim():
    assert "Answer yes or no." in vcm._SAME_SUBJECT_PROMPT
    assert "Do these two images show the same person or character?" in vcm._SAME_SUBJECT_PROMPT


# ── The harness and the gate describe the same row ───────────────────────────

@pytest.fixture(scope="module")
def harness():
    """scripts/eval_identity_methods.py, loaded by path — scripts/ is not a package."""
    spec = importlib.util.spec_from_file_location(
        "eval_identity_methods",
        Path(__file__).resolve().parents[3] / "scripts" / "eval_identity_methods.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_prints_the_row_the_gate_reads(harness):
    """A row the gate cannot read is a row nobody can paste in."""
    row = harness.measured_row({"method": "vlm", "model": "vlm-test:latest",
                                "threshold": data.IDENTITY_METHODS["vlm"]["threshold"],
                                "correct": 96, "n": 100})
    assert set(row) == {"model", "digest", "threshold", "correct", "n", "date"}

    with patch.dict(data.IDENTITY_METHODS["vlm"], {"measured": [row]}):
        assert data.proven_for_model("vlm", "vlm-test:latest") is True


def test_the_harness_refuses_to_run_while_the_gpu_is_held(harness, capsys):
    """It loads a vision model; a render or a training run must not have to share."""
    with patch.object(harness, "gpu_is_busy", return_value="lora_train (smoke_7)"):
        assert harness.main([]) == 2
    assert "not starting" in capsys.readouterr().err


def test_the_harness_builds_balanced_labelled_pairs(harness):
    subjects = [
        (1, "A", ["a1.png", "a2.png"]),
        (2, "B", ["b1.png", "b2.png"]),
        (3, "C", ["c1.png"]),  # one image cannot make a positive
    ]
    pairs = harness.build_pairs(subjects, limit=4, rng=random.Random(0))

    assert sum(1 for p in pairs if p["same"]) == 2
    assert sum(1 for p in pairs if not p["same"]) == 2
    assert all(p["subjects"][0] == p["subjects"][1] for p in pairs if p["same"])
    assert all(3 not in p["subjects"] for p in pairs)


def test_the_harness_reports_both_distributions_and_the_best_cut(harness):
    scores = [{"same": True, "raw_score": v} for v in (0.5, 0.6, 0.7)]
    scores += [{"same": False, "raw_score": v} for v in (0.1, 0.2, 0.3)]
    cut = harness.best_cut(scores)
    assert cut["threshold"] == pytest.approx(0.4) and cut["gap"] == pytest.approx(0.2)
    assert cut["correct"] == 6

    overlapping = scores + [{"same": False, "raw_score": 0.65}]
    cut = harness.best_cut(overlapping)
    assert cut["gap"] is None and cut["correct"] == 6
    assert harness.distribution([0.1, 0.3, 0.2]) == {"min": 0.1, "median": 0.2, "max": 0.3, "n": 3}


def test_the_harness_does_not_ask_the_gpu_for_cpu_methods(harness, monkeypatch):
    """embed and hist run on the CPU, so a render holding the card does not stop them."""
    monkeypatch.setattr(harness, "gpu_is_busy", lambda: pytest.fail("asked about the GPU"))
    monkeypatch.setattr(harness, "subject_images", lambda kind: [])
    assert harness.main(["--methods", "embed,hist"]) == 1


def test_the_harness_asks_the_running_backend_whether_the_gpu_is_held(harness):
    """The job gate lives in the backend process; one built in the script reads free."""
    from backend.utils import backend_http
    held = backend_http.BackendResponse(200, None, {
        "gpu_busy": True, "gpu_holder": {"kind": "lora_train", "native_id": "smoke_7"}})
    with patch.object(backend_http, "request_json", return_value=held) as ask:
        assert harness.gpu_is_busy() == "lora_train (smoke_7)"
    assert ask.call_args.args == ("GET", "/api/jobs/gate")
    down = backend_http.BackendError("unreachable", "not answering")
    with patch.object(backend_http, "request_json", side_effect=down):
        assert harness.gpu_is_busy() is None
