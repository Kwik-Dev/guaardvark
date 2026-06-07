"""Regression: GET /api/jobs must never 500 on an enum left in a Job.

Twice now an enum leaked into the wire format and broke jsonify for the WHOLE
list response: 2026-06-02 ProcessStatus, 2026-06-03 ProcessType (a raw
ProcessType in adapt_unified_progress -> Job.metadata). These pin both the
source coercion and the defensive net in Job.to_dict.
"""
import json
from enum import Enum

import pytest

from backend.services.job_types import Job, JobKind, JobStatus, _json_safe
from backend.services import job_registry as jr


class _PT(str, Enum):
    CSV = "csv_processing"
    OUTREACH = "outreach"


def test_to_dict_is_json_serializable_with_enum_in_metadata():
    j = Job(
        id="unified:x", kind=JobKind.UNIFIED_PROGRESS, native_id="x",
        status=JobStatus.RUNNING, label="t",
        metadata={"process_type": _PT.CSV, "nested": {"k": _PT.OUTREACH}},
    )
    d = j.to_dict()
    json.dumps(d)  # would raise TypeError before the fix
    assert d["metadata"]["process_type"] == "csv_processing"
    assert d["metadata"]["nested"]["k"] == "outreach"


def test_adapt_unified_progress_coerces_enum_process_type():
    job = jr.adapt_unified_progress(
        {"process_id": "p1", "process_type": _PT.CSV, "status": "running", "progress": 50}
    )
    json.dumps(job.to_dict())
    assert job.metadata["process_type"] == "csv_processing"


def test_outreach_classified_when_process_type_is_enum():
    # The pre-fix `enum == "outreach"` was always False -> outreach misclassified.
    job = jr.adapt_unified_progress({"process_id": "task_9", "process_type": _PT.OUTREACH, "status": "running"})
    assert job.kind == JobKind.OUTREACH


def test_json_safe_leaves_plain_values_untouched():
    assert _json_safe("x") == "x"
    assert _json_safe(5) == 5
    assert _json_safe([1, "a"]) == [1, "a"]
