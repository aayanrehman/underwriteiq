"""Deterministic validation of submissions — no LLM judgment involved.

Checks required-field completeness and basic type/range sanity so an
underwriter immediately sees what's missing or malformed before any
scoring happens.
"""
from __future__ import annotations

from .models import Submission, ValidationResult

# Fields every submission must provide for a full assessment.
REQUIRED_FIELDS = [
    "submission_id",
    "applicant.business_type",
    "applicant.years_in_business",
    "coverage_requested.line_of_business",
    "coverage_requested.limit",
    "coverage_requested.deductible",
]

# Fields that should be numeric if present.
NUMERIC_FIELDS = [
    "applicant.years_in_business",
    "applicant.annual_revenue",
    "coverage_requested.limit",
    "coverage_requested.deductible",
]

# Sanity ranges: (min, max). None means unbounded on that side.
RANGE_CHECKS: dict[str, tuple[float | None, float | None]] = {
    "applicant.years_in_business": (0, 100),
    "applicant.annual_revenue": (0, None),
    "coverage_requested.limit": (0, None),
    "coverage_requested.deductible": (0, None),
}

# Loss-run entries need these keys to be usable.
LOSS_RUN_REQUIRED_KEYS = ["year", "loss_amount"]


def validate(submission: Submission) -> ValidationResult:
    result = ValidationResult()

    for path in REQUIRED_FIELDS:
        value = submission.field(path)
        if value is None or value == "":
            result.missing_fields.append(path)

    for path in NUMERIC_FIELDS:
        value = submission.field(path)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            result.invalid_fields.append(f"{path} (not numeric: {value!r})")
            continue
        low, high = RANGE_CHECKS.get(path, (None, None))
        if low is not None and value < low:
            result.invalid_fields.append(f"{path} below minimum {low}: {value}")
        if high is not None and value > high:
            result.invalid_fields.append(f"{path} above maximum {high}: {value}")

    for i, run in enumerate(submission.loss_runs):
        for key in LOSS_RUN_REQUIRED_KEYS:
            if key not in run or run[key] is None:
                result.missing_fields.append(f"loss_runs[{i}].{key}")

    # Warning-level signals (don't block, but flag).
    if not submission.loss_runs:
        result.warnings.append("No prior loss runs provided; loss history unverified.")
    if submission.field("applicant.years_in_business") is None:
        result.warnings.append("Years in business unknown; stability cannot be assessed.")

    return result
