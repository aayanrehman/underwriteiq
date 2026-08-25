"""Optional REST API exposing POST /assess.

Run:  uvicorn underwriteiq.api:app --port 8000
Then: curl -X POST localhost:8000/assess -d @data/samples/submission_bluebird_clean.json \
        -H "Content-Type: application/json"

The /assess endpoint is fully deterministic. The LLM agent layer is not
exposed here by design.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import Store, Submission, assess_submission, evaluate_all, validate
from .guidelines import DEFAULT_RULES

app = FastAPI(title="UnderwriteIQ", version="0.1.0")


class SubmissionIn(BaseModel):
    submission_id: str
    applicant: dict[str, Any] = {}
    coverage_requested: dict[str, Any] = {}
    loss_runs: list[dict[str, Any]] = []
    property_details: dict[str, Any] = {}
    liability_details: dict[str, Any] = {}


@app.post("/assess")
def assess(payload: SubmissionIn) -> dict:
    try:
        sub = Submission(
            submission_id=payload.submission_id,
            applicant=payload.applicant,
            coverage_requested=payload.coverage_requested,
            loss_runs=payload.loss_runs,
            property_details=payload.property_details,
            liability_details=payload.liability_details,
        )
        store = Store()
        rules = store.load_rules()
        seeded = bool(rules)
        if not rules:
            for r in DEFAULT_RULES:
                store.save_rule(r)
            rules = DEFAULT_RULES

        validation = validate(sub)
        results = evaluate_all(rules, sub)
        assessment = assess_submission(sub.submission_id, results, validation)
        store.save_submission(sub)
        store.save_assessment(assessment)

        return {
            "submission_id": assessment.submission_id,
            "recommendation": assessment.recommendation,
            "risk_score": assessment.risk_score,
            "matched_rules": [
                {"rule_id": r.rule_id, "outcome": r.outcome, "observed": repr(r.observed_value)}
                for r in assessment.matched_rules
            ],
            "validation": {
                "complete": validation.is_complete,
                "missing_fields": validation.missing_fields,
                "invalid_fields": validation.invalid_fields,
                "warnings": validation.warnings,
            },
            "rationale_points": assessment.rationale_points,
            "rules_seeded_on_first_call": not seeded,
        }
    except Exception as exc:  # narrow surface for a demo API
        raise HTTPException(status_code=400, detail=str(exc)) from exc
