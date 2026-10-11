"""Seed a default synthetic underwriting guideline ruleset.

These rules are illustrative only — they are not any real carrier's
appetite guidelines.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .models import GuidelineRule

DEFAULT_RULES = [
    GuidelineRule(
        rule_id="GL-001",
        description="Property line outside current appetite",
        field="coverage_requested.line_of_business",
        operator="in",
        value=["property", "commercial_property"],
        outcome="refer",
        weight=20,
        section="appetite",
    ),
    GuidelineRule(
        rule_id="GL-002",
        description="Business younger than 2 years",
        field="applicant.years_in_business",
        operator="lt",
        value=2,
        outcome="refer",
        weight=25,
        section="stability",
    ),
    GuidelineRule(
        rule_id="GL-003",
        description="Requested limit above $5M authority level",
        field="coverage_requested.limit",
        operator="gt",
        value=5_000_000,
        outcome="decline",
        weight=60,
        section="authority",
    ),
    GuidelineRule(
        rule_id="GL-004",
        description="More than one prior loss in last 3 years",
        field="derived.loss_count_3y",
        operator="gt",
        value=1,
        outcome="surcharge",
        weight=15,
        section="loss_history",
    ),
    GuidelineRule(
        rule_id="GL-005",
        description="High-hazard occupancy class",
        field="applicant.business_type",
        operator="in",
        value=["demolition", "explosives_manufacturing", "scrap_yard"],
        outcome="decline",
        weight=50,
        section="appetite",
    ),
    GuidelineRule(
        rule_id="GL-006",
        description="Single loss of $100k or more in last 3 years",
        field="derived.max_single_loss",
        operator="gte",
        value=100_000,
        outcome="refer",
        weight=30,
        section="loss_history",
    ),
    GuidelineRule(
        rule_id="GL-007",
        description="Unsprinklered property risk",
        field="property_details.sprinklered",
        operator="eq",
        value=False,
        outcome="refer",
        weight=20,
        section="property",
    ),
    GuidelineRule(
        rule_id="GL-008",
        description="Requested limit above 4x annual revenue",
        field="derived.limit_to_revenue",
        operator="gt",
        value=4,
        outcome="surcharge",
        weight=15,
        section="exposure",
    ),
]


def ruleset_version(rules: list[GuidelineRule]) -> str:
    """Content hash of a ruleset, stamped on every assessment for audit.

    Same rules -> same version, regardless of list order.
    """
    canonical = json.dumps(sorted((asdict(r) for r in rules), key=lambda r: r["rule_id"]),
                           sort_keys=True, default=str)
    return "rs-" + hashlib.sha256(canonical.encode()).hexdigest()[:10]
