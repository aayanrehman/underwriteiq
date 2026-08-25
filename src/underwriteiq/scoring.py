"""Deterministic scoring math: turn rule results into a recommendation.

The aggregation logic is deliberately simple and explainable:
  - Decline wins outright if any matched rule says decline.
  - Otherwise refer if any matched rule says refer, or if the risk score
    crosses REFER_THRESHOLD, or if validation found blocking problems.
  - Otherwise accept.
Risk score = min(RAW_SCORE_CAP, sum of weights of matched rules), scaled
to 0-100 against MAX_SCORE_WEIGHTS.
"""
from __future__ import annotations

from .models import Assessment, RuleResult, ValidationResult

REFER_THRESHOLD = 40          # score at/above this refers even without a refer rule
MAX_SCORE_WEIGHTS = 60        # matched-rule weight total that maps to score 100
RAW_SCORE_CAP = 100

PRIORITY = {"decline": 3, "refer": 2, "accept": 1}


def compute_risk_score(results: list[RuleResult]) -> float:
    total_weight = sum(r.weight for r in results if r.matched)
    capped = min(total_weight, MAX_SCORE_WEIGHTS)
    score = round(capped / MAX_SCORE_WEIGHTS * RAW_SCORE_CAP, 1)
    return score


def decide_recommendation(
    results: list[RuleResult],
    validation: ValidationResult,
    risk_score: float,
) -> str:
    outcomes = [r.outcome for r in results if r.matched]
    if "decline" in outcomes:
        return "decline"
    if validation.missing_fields or validation.invalid_fields:
        return "refer"  # can't responsibly auto-accept incomplete data
    if "refer" in outcomes:
        return "refer"
    if risk_score >= REFER_THRESHOLD:
        return "refer"
    return "accept"


def assess_submission(
    submission_id: str,
    results: list[RuleResult],
    validation: ValidationResult,
) -> Assessment:
    risk_score = compute_risk_score(results)
    recommendation = decide_recommendation(results, validation, risk_score)

    rationale: list[str] = []
    for r in results:
        if r.matched:
            rationale.append(
                f"MATCHED {r.citation()}: observed={r.observed_value!r} "
                f"-> guideline outcome '{r.outcome}' (+{r.weight} risk)"
            )
    for m in validation.missing_fields:
        rationale.append(f"MISSING DATA: {m}")
    for i in validation.invalid_fields:
        rationale.append(f"INVALID DATA: {i}")
    if not rationale:
        rationale.append("No guideline rules matched; submission within appetite.")

    return Assessment(
        submission_id=submission_id,
        recommendation=recommendation,
        risk_score=risk_score,
        rule_results=results,
        validation=validation,
        rationale_points=rationale,
    )
