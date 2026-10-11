"""Seeded synthetic book of business with a latent loss process.

Each generated applicant has a hidden annual claim frequency driven by its
class hazard, tenure, exposure and an unobserved "risk quality" term. That
same frequency produces both the 3-year loss runs the underwriter sees and
the *next-year* outcome the underwriter can't see. The simulator scores
rulesets against those outcomes, so "did this rule change improve the loss
ratio?" has a ground-truth answer.

Roughly 5% of submissions carry injected data-quality defects (missing
fields, string-typed numbers, negative values) so validation has work to do.

Deterministic: same (n, seed) -> byte-identical book.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .features import AS_OF_YEAR
from .rating import CLASSES

CLASS_WEIGHTS = {
    "accounting": 9, "software_consulting": 9, "design_studio": 7, "dental_office": 7,
    "retail": 12, "landscaping": 11, "restaurant": 11, "auto_repair": 8,
    "general_contractor": 9, "roofing": 6, "trucking": 6, "scrap_yard": 2,
    "demolition": 2, "explosives_manufacturing": 1,
}
PROFESSIONAL = {"accounting", "software_consulting", "design_studio", "dental_office"}
STATES = ["CA", "TX", "FL", "NY", "IL", "PA", "OH", "GA", "NC", "MI", "AZ", "WA", "CO", "MN"]
STATE_WEIGHTS = [14, 11, 10, 9, 6, 6, 5, 5, 5, 4, 4, 4, 3, 3]
LIMITS = [500_000, 1_000_000, 2_000_000, 3_000_000, 5_000_000, 10_000_000]
LIMIT_WEIGHTS = [8, 45, 22, 10, 10, 5]
DEDUCTIBLES = [1_000, 2_500, 5_000, 10_000, 25_000]
DEDUCTIBLE_WEIGHTS = [15, 25, 35, 15, 10]

_ADJ = ["Bluebird", "Cobalt", "Granite", "Juniper", "Harbor", "Summit", "Copper", "Maple",
        "Northwind", "Ironwood", "Silverline", "Cedar", "Redrock", "Lakeshore", "Prairie",
        "Beacon", "Willow", "Keystone", "Atlas", "Meridian", "Sable", "Driftwood"]
_NOUN = ["Ridge", "Valley", "Point", "Creek", "Works", "Partners", "Group", "Brothers",
         "Collective", "Co-op", "Labs", "Field", "Bay", "Stone"]
_SUFFIX = ["LLC", "Inc.", "Co.", "Ltd.", "& Sons", "Group LLC"]


@dataclass(frozen=True)
class Outcome:
    """Next-year loss experience: what actually happens if we bind."""
    submission_id: str
    claim_count: int
    incurred: float


def _poisson(rng: random.Random, lam: float) -> int:
    # Knuth; fine for the small rates used here
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _severity(rng: random.Random, hazard: float, line: str) -> float:
    scale = 2.4 if line == "commercial_property" else 1.0  # fires cost more than slip-and-falls
    return round(rng.lognormvariate(math.log(14_000 * hazard * scale), 1.15), 2)


def _line(rng: random.Random, btype: str) -> str:
    if btype in PROFESSIONAL:
        return rng.choices(["professional_liability", "general_liability", "cyber",
                            "commercial_property"], [45, 25, 20, 10])[0]
    return rng.choices(["general_liability", "commercial_property", "cyber",
                        "professional_liability"], [55, 30, 10, 5])[0]


def _inject_defect(rng: random.Random, raw: dict) -> None:
    kind = rng.randrange(5)
    if kind == 0:
        raw["applicant"].pop("years_in_business", None)
    elif kind == 1:
        raw["coverage_requested"]["limit"] = f"{raw['coverage_requested']['limit']:,}"
    elif kind == 2:
        raw["coverage_requested"].pop("deductible", None)
    elif kind == 3:
        raw["applicant"]["annual_revenue"] = -abs(raw["applicant"]["annual_revenue"])
    else:
        raw["applicant"].pop("business_type", None)


def generate_book(n: int = 5_000, seed: int = 42,
                  defect_rate: float = 0.05) -> tuple[list[dict], dict[str, Outcome]]:
    rng = random.Random(seed)
    classes, class_w = zip(*CLASS_WEIGHTS.items(), strict=True)
    book: list[dict] = []
    outcomes: dict[str, Outcome] = {}

    for i in range(1, n + 1):
        sid = f"BK-{seed}-{i:05d}"
        btype = rng.choices(classes, class_w)[0]
        hazard, median_rev = CLASSES[btype]
        years = min(60, int(rng.expovariate(1 / 9)))
        revenue = int(round(median_rev * rng.lognormvariate(0, 0.6), -3)) or 50_000
        employees = max(1, int(revenue / rng.uniform(80_000, 220_000)))
        line = _line(rng, btype)
        limit = rng.choices(LIMITS, LIMIT_WEIGHTS)[0]
        if hazard > 1.6 and rng.random() < 0.3:
            limit = rng.choice([5_000_000, 10_000_000])  # heavy risks shop big towers

        prop: dict = {}
        if line == "commercial_property":
            prop = {
                "construction_class": rng.choices(
                    ["fire_resistive", "masonry", "joisted_masonry", "frame"], [20, 30, 25, 25])[0],
                "sprinklered": rng.random() < 0.7,
            }

        # latent annual claim frequency
        quality = rng.lognormvariate(0, 0.55)              # unobserved by underwriting
        lam = 0.055 * hazard * quality * (revenue / 1_000_000) ** 0.35
        lam *= 1.45 if years < 3 else 1.0
        if prop and not prop["sprinklered"]:
            lam *= 1.3 if prop["construction_class"] == "frame" else 1.15

        loss_runs = []
        for year in range(AS_OF_YEAR - 2, AS_OF_YEAR + 1):
            if AS_OF_YEAR - year > years:
                continue  # wasn't operating yet
            count = _poisson(rng, lam)
            if count == 0:
                if rng.random() < 0.5:
                    loss_runs.append({"year": year, "loss_amount": 0, "description": "no claims"})
                continue
            for _ in range(count):
                loss_runs.append({"year": year, "loss_amount": _severity(rng, hazard, line),
                                  "description": "reported claim"})

        future_count = _poisson(rng, lam)
        incurred = min(sum(_severity(rng, hazard, line) for _ in range(future_count)), limit)
        outcomes[sid] = Outcome(sid, future_count, round(incurred, 2))

        raw = {
            "submission_id": sid,
            "applicant": {
                "name": f"{rng.choice(_ADJ)} {rng.choice(_NOUN)} "
                        f"{btype.replace('_', ' ').title()} {rng.choice(_SUFFIX)}",
                "business_type": btype,
                "state": rng.choices(STATES, STATE_WEIGHTS)[0],
                "years_in_business": years,
                "annual_revenue": revenue,
                "employees": employees,
            },
            "coverage_requested": {
                "line_of_business": line,
                "limit": limit,
                "deductible": rng.choices(DEDUCTIBLES, DEDUCTIBLE_WEIGHTS)[0],
            },
            "loss_runs": loss_runs,
            "property_details": prop,
            "liability_details": {},
        }
        if rng.random() < defect_rate:
            _inject_defect(rng, raw)
        book.append(raw)

    return book, outcomes
