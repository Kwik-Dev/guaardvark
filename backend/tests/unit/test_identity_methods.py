"""The Cast identity check reports a number only when it has earned one.

The method these replace divided the smoke PNG's byte count by the mean
reference byte count and showed the result as an identity score, green above
0.75. These tests pin the three things that stop that returning: the smoke path
cannot reach "size", a method with no measured rows cannot show a score, and a
model that fails or will not answer cannot produce a match.

Every model call is mocked. Nothing here loads weights, reaches Ollama, or
touches the network.
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from backend.services import identity_method_data as data
from backend.services import video_consistency_metrics as vcm


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
    ref, cand = images
    identity = vcm.score_smoke_identity([ref], cand)

    assert identity["status"] == "not_measured"
    assert identity["score"] is None
    assert identity["method"] is None
    assert identity["reason"] == data.NOT_PROVEN_REASON


def test_smoke_path_does_not_consult_the_size_method(images):
    """Shipped state: no method has rows, so nothing runs and nothing is scored."""
    ref, cand = images
    with patch.object(vcm, "score_identity_preservation") as scorer:
        vcm.score_smoke_vs_refs([ref], cand)
    assert scorer.call_args_list == []


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


# ── embed without weights ────────────────────────────────────────────────────

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


def test_embed_without_weights_is_not_measured_and_asks_for_local_files_only(images):
    ref, cand = images
    processor, model = _Loader(), _Loader()
    with patch.object(vcm, "_image_encoder_classes", return_value=(processor, model)):
        result = vcm.score_identity_preservation([ref], cand, method="embed")

    assert result["status"] == "not_measured"
    assert result["score"] is None
    assert result["reason"] == "image encoder not installed"

    assert processor.calls, "the encoder load was attempted"
    for _name, kwargs in processor.calls + model.calls:
        assert kwargs.get("local_files_only") is True
    assert not vcm._ENCODER_CACHE, "a failed load is not cached"


def test_embed_never_reaches_the_hub_or_a_download_path(images):
    """The identity check is not allowed to be the thing that starts a download."""
    ref, cand = images
    processor, model = _Loader(), _Loader()
    with patch("backend.services.local_weights.is_cached") as cached, \
            patch.object(vcm, "_image_encoder_classes", return_value=(processor, model)):
        result = vcm.score_identity_preservation([ref], cand, method="embed")

    assert result["status"] == "not_measured"
    assert cached.call_args_list == [], "no cache or hub probe on this path"


# ── The evidence gate ────────────────────────────────────────────────────────

def test_every_shipped_method_has_an_empty_measured_list():
    for method, spec in data.IDENTITY_METHODS.items():
        assert spec["measured"] == [], f"{method} ships with evidence it did not earn"
        assert spec["floor"] >= data.DEFAULT_IDENTITY_FLOOR
        assert 0.0 < spec["threshold"] < 1.0


def test_empty_measured_list_is_not_proven():
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
    row = harness.measured_row({"model": "vlm-test:latest", "correct": 96, "n": 100})
    assert set(row) == {"model", "digest", "correct", "n", "date"}

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
