"""Seed a default synthetic underwriting guideline ruleset.

These rules are illustrative only — they are not any real carrier's
appetite guidelines.
"""
from __future__ import annotations

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
        field="loss_runs",
        operator="not_in",
        value=[[], None],
        outcome="surcharge",
        weight=10,
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
]
