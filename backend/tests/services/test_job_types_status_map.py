"""map_status knows every status word each job source actually writes.

Unknown words deliberately map to FAILED (a red row beats a silent "running
forever"), so a kind whose native vocabulary is missing from the table shows
all of its finished runs as failed. That is what happened to self-improvement
("success", "no_change") and autoresearch experiments ("keep", "discard").
"""
import pytest

from backend.services.job_types import JobKind, JobStatus, map_status


@pytest.mark.parametrize("native, expected", [
    ("running", JobStatus.RUNNING),
    ("success", JobStatus.COMPLETED),
    ("no_change", JobStatus.COMPLETED),
    ("failed", JobStatus.FAILED),
    ("blocked_by_guardian", JobStatus.CANCELLED),
    ("cancelled", JobStatus.CANCELLED),
])
def test_self_improvement_words(native, expected):
    assert map_status(JobKind.SELF_IMPROVEMENT, native) == expected


@pytest.mark.parametrize("native, expected", [
    ("keep", JobStatus.COMPLETED),
    ("discard", JobStatus.COMPLETED),
    ("crash", JobStatus.FAILED),
])
def test_experiment_words(native, expected):
    assert map_status(JobKind.EXPERIMENT, native) == expected


def test_unknown_word_still_fails_loudly():
    assert map_status(JobKind.SELF_IMPROVEMENT, "made_up") == JobStatus.FAILED
    assert map_status(JobKind.EXPERIMENT, "made_up") == JobStatus.FAILED


def test_empty_status_is_pending():
    assert map_status(JobKind.SELF_IMPROVEMENT, None) == JobStatus.PENDING
    assert map_status(JobKind.EXPERIMENT, "") == JobStatus.PENDING
