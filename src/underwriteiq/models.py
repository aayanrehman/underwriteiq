"""Data models for submissions, guideline rules, and assessments.

Deterministic layer — no LLM involvement. These dataclasses define the
contract between raw submission JSON, the SQL store, the rule engine,
and the scoring math.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Submission:
    """A commercial or personal lines insurance submission (synthetic data only)."""

    submission_id: str
    applicant: dict[str, Any]          # name, business_type, years_in_business, ...
    coverage_requested: dict[str, Any]  # line_of_business, limit, deductible, ...
    loss_runs: list[dict[str, Any]] = field(default_factory=list)  # prior losses
    property_details: dict[str, Any] = field(default_factory=dict)
    liability_details: dict[str, Any] = field(default_factory=dict)

    def field(self, dotted: str, default: Any = None) -> Any:
        """Look up a nested value by dot path, e.g. 'coverage_requested.limit'."""
        node: Any = self
        for part in dotted.split("."):
            if isinstance(node, Submission):
                if not hasattr(node, part):
                    return default
                node = getattr(node, part)
            elif isinstance(node, dict):
                if part not in node:
                    return default
                node = node[part]
            else:
                return default
        return node


@dataclass
class GuidelineRule:
    """One underwriting guideline rule.

    A rule compares a submission field against a threshold using a simple
    operator. Operators: gte, lte, gt, lt, eq, neq, in, not_in.
    `outcome` is what the guideline says when the condition matches.
    """
    rule_id: str
    description: str
    field: str              # dotted path into a Submission
    operator: str
    value: Any              # comparison target
    outcome: str            # "accept" | "decline" | "refer" | "surcharge"
    weight: int = 1         # contribution to risk score when matched
    section: str = "general"


@dataclass
class RuleResult:
    """Outcome of evaluating one rule against one submission."""
    rule_id: str
    description: str
    matched: bool
    observed_value: Any
    outcome: str
    weight: int

    def citation(self) -> str:
        return f"{self.rule_id} ({self.description})"


@dataclass
class ValidationResult:
    """Deterministic data-quality report for a submission."""
    missing_fields: list[str] = field(default_factory=list)
    invalid_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields and not self.invalid_fields


@dataclass
class Assessment:
    """Full deterministic assessment of a submission."""
    submission_id: str
    recommendation: str                 # accept / refer / decline
    risk_score: float                   # 0-100, higher = more risk
    rule_results: list[RuleResult] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=ValidationResult)
    rationale_points: list[str] = field(default_factory=list)

    @property
    def matched_rules(self) -> list[RuleResult]:
        return [r for r in self.rule_results if r.matched]
