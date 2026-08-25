"""Deterministic guideline-rule matching against submissions.

Each GuidelineRule is a single condition over a submission field.
Matching is pure arithmetic/comparison — fully unit-testable, no LLM.
"""
from __future__ import annotations

import operator as op

from .models import GuidelineRule, RuleResult, Submission

_OPERATORS = {
    "gte": op.ge,
    "lte": op.le,
    "gt": op.gt,
    "lt": op.lt,
    "eq": op.eq,
    "neq": op.ne,
}


def _compare(observed, operator_name: str, target) -> bool:
    if operator_name == "in":
        try:
            return observed in target
        except TypeError:
            return False
    if operator_name == "not_in":
        try:
            return observed not in target
        except TypeError:
            return True
    fn = _OPERATORS.get(operator_name)
    if fn is None:
        raise ValueError(f"Unknown operator: {operator_name!r}")
    if observed is None:
        return False  # missing fields never match; they're flagged by validation instead
    try:
        return bool(fn(observed, target))
    except TypeError:
        return False


def evaluate_rule(rule: GuidelineRule, submission: Submission) -> RuleResult:
    observed = submission.field(rule.field)
    matched = _compare(observed, rule.operator, rule.value)
    return RuleResult(
        rule_id=rule.rule_id,
        description=rule.description,
        matched=matched,
        observed_value=observed,
        outcome=rule.outcome if matched else "no_match",
        weight=rule.weight if matched else 0,
    )


def evaluate_all(rules: list[GuidelineRule], submission: Submission) -> list[RuleResult]:
    return [evaluate_rule(r, submission) for r in rules]
