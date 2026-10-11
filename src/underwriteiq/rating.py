"""Rating worksheet: indicated annual premium from a manual of factors.

Modeled on the rating-manual idea in vendor/aspire (base rate x a chain of
multiplicative factors) but written from scratch over plain dicts. Every
factor is returned so the UI can show a step-by-step worksheet.

All rates are illustrative, not any carrier's filed rates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Submission

# business_type -> (hazard factor, median annual revenue in the synthetic book)
CLASSES: dict[str, tuple[float, float]] = {
    "accounting":               (0.55, 900_000),
    "software_consulting":      (0.60, 1_400_000),
    "design_studio":            (0.65, 600_000),
    "dental_office":            (0.80, 1_100_000),
    "retail":                   (0.90, 1_300_000),
    "landscaping":              (1.00, 800_000),
    "restaurant":               (1.15, 1_500_000),
    "auto_repair":              (1.30, 1_000_000),
    "general_contractor":       (1.45, 2_600_000),
    "roofing":                  (1.85, 1_700_000),
    "trucking":                 (1.70, 3_200_000),
    "scrap_yard":               (2.20, 2_000_000),
    "demolition":               (2.40, 2_300_000),
    "explosives_manufacturing": (3.00, 4_000_000),
}

# line -> base rate per $1,000 of revenue at a $1M limit
BASE_RATES = {
    "general_liability":      2.10,
    "professional_liability": 3.40,
    "commercial_property":    2.80,
    "cyber":                  1.60,
}

# increased-limits factors (limit -> multiplier); interpolated between points
ILF = [(500_000, 0.80), (1_000_000, 1.00), (2_000_000, 1.28), (3_000_000, 1.45),
       (5_000_000, 1.68), (10_000_000, 2.05)]

MIN_PREMIUM = 750.0


@dataclass
class RatingWorksheet:
    premium: float | None
    steps: list[tuple[str, float]] = field(default_factory=list)  # (label, value)
    reason: str = ""


def _ilf(limit: float) -> float:
    if limit <= ILF[0][0]:
        return ILF[0][1]
    for (l0, f0), (l1, f1) in zip(ILF, ILF[1:], strict=False):
        if limit <= l1:
            return round(f0 + (f1 - f0) * (limit - l0) / (l1 - l0), 4)
    return ILF[-1][1]


def _deductible_credit(deductible: float) -> float:
    # 2.5% credit per doubling above $1k, capped at 20%
    if deductible <= 1_000:
        return 1.0
    doublings = int(deductible // 1_000).bit_length() - 1
    return round(max(0.80, 1 - 0.025 * doublings), 4)


def _experience_mod(loss_count: int, years: float) -> float:
    mod = 1.0 + 0.15 * loss_count
    if years < 3:
        mod += 0.10  # thin experience period
    return round(min(mod, 1.75), 4)


def rate(sub: Submission) -> RatingWorksheet:
    lob = sub.field("coverage_requested.line_of_business")
    revenue = sub.field("applicant.annual_revenue")
    limit = sub.field("coverage_requested.limit")
    deductible = sub.field("coverage_requested.deductible") or 0
    years = sub.field("applicant.years_in_business")
    btype = sub.field("applicant.business_type")

    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                  for v in (revenue, limit, deductible, years))
    if lob not in BASE_RATES or btype not in CLASSES or not numeric or revenue <= 0:
        return RatingWorksheet(None, reason="insufficient or unratable data")

    hazard = CLASSES[btype][0]
    base = BASE_RATES[lob] * revenue / 1_000
    steps = [
        (f"Base rate {BASE_RATES[lob]:.2f} per $1k revenue", round(base, 2)),
        (f"Class hazard ({btype})", hazard),
        ("Increased limits factor", _ilf(limit)),
        ("Deductible credit", _deductible_credit(deductible)),
        ("Experience mod", _experience_mod(sub.derived["loss_count_3y"], years)),
    ]
    premium = base
    for _, factor in steps[1:]:
        premium *= factor
    premium = round(max(premium, MIN_PREMIUM), 2)
    steps.append(("Indicated premium", premium))
    return RatingWorksheet(premium, steps)
