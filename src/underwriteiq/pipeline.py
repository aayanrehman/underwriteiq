#!/usr/bin/env python3
"""End-to-end pipeline: load submission JSON -> validate -> rule match ->
score -> persist -> print assessment. Deterministic; no LLM calls.

Usage:
    python -m underwriteiq.pipeline data/samples/submission_acme_clean.json

Optional env:
    UNDERWRITEIQ_DB   path to SQLite file (default data/underwriteiq.db)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# allow running as a plain script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from underwriteiq import (  # noqa: E402
    Store, Submission, assess_submission, evaluate_all, validate,
)
from underwriteiq.guidelines import DEFAULT_RULES  # noqa: E402


def load_submission(path: str) -> Submission:
    raw = json.loads(Path(path).read_text())
    return Submission(
        submission_id=raw["submission_id"],
        applicant=raw.get("applicant", {}),
        coverage_requested=raw.get("coverage_requested", {}),
        loss_runs=raw.get("loss_runs", []),
        property_details=raw.get("property_details", {}),
        liability_details=raw.get("liability_details", {}),
    )


def run_assessment(sub: Submission, store: Store):
    rules = store.load_rules()
    if not rules:
        for r in DEFAULT_RULES:
            store.save_rule(r)
        rules = DEFAULT_RULES

    validation = validate(sub)
    results = evaluate_all(rules, sub)
    assessment = assess_submission(sub.submission_id, results, validation)

    store.save_submission(sub)
    store.save_assessment(assessment)
    return assessment


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    sub = load_submission(argv[1])
    store = Store()
    assessment = run_assessment(sub, store)

    print(f"Submission : {assessment.submission_id}")
    print(f"Validation : complete={assessment.validation.is_complete} "
          f"missing={assessment.validation.missing_fields} "
          f"invalid={assessment.validation.invalid_fields}")
    for w in assessment.validation.warnings:
        print(f"  warning  : {w}")
    print(f"Risk score : {assessment.risk_score}")
    print(f"Recommendation: {assessment.recommendation.upper()}")
    print("Rationale:")
    for point in assessment.rationale_points:
        print(f"  - {point}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
