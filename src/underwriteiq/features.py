"""Feature engineering over raw submissions.

Guideline rules, the rating worksheet and the loss model all read the same
derived features, so "loss count" means one thing everywhere. Rules address
these as `derived.<name>` (e.g. `derived.loss_count_3y`).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Submission

AS_OF_YEAR = 2026        # experience period ends here (synthetic book year)
EXPERIENCE_YEARS = 3


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def derive(sub: Submission) -> dict[str, Any]:
    window = range(AS_OF_YEAR - EXPERIENCE_YEARS + 1, AS_OF_YEAR + 1)
    amounts = [
        _num(run.get("loss_amount")) or 0.0
        for run in sub.loss_runs
        if run.get("year") in window
    ]
    claims = [a for a in amounts if a > 0]

    revenue = _num(sub.applicant.get("annual_revenue"))
    employees = _num(sub.applicant.get("employees"))
    limit = _num(sub.coverage_requested.get("limit"))

    return {
        "loss_count_3y": len(claims),
        "total_incurred_3y": round(sum(claims), 2),
        "max_single_loss": max(claims, default=0.0),
        "revenue_per_employee": round(revenue / employees, 2) if revenue and employees else None,
        "limit_to_revenue": round(limit / revenue, 3) if limit and revenue else None,
        "log_revenue": round(math.log10(revenue), 4) if revenue and revenue > 0 else None,
    }
