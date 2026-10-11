"""Portfolio what-if simulator.

Re-underwrites the whole synthetic book under a modified ruleset and scores
the result against the hidden next-year outcomes:

    baseline = simulate(book, DEFAULT_RULES)
    scenario = simulate(book, apply_overrides(DEFAULT_RULES, {"GL-003": {"value": 10_000_000}}))

Validation, rating and the fields rules read don't depend on the rules, so
they're computed once per submission in `Book`; only rule matching and the
decision cascade rerun per scenario (5,000 submissions in ~20 ms).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .models import GuidelineRule, RuleResult, Submission, ValidationResult
from .rating import rate
from .rules import _compare
from .scoring import REFER_THRESHOLD, compute_risk_score, decide_recommendation
from .validation import validate

EXPENSE_RATIO = 0.30  # acquisition + admin, illustrative


@dataclass
class _Row:
    sub: Submission
    validation: ValidationResult
    premium: float | None
    incurred: float
    line: str
    observed: dict  # rule field -> value; inputs don't change between scenarios

    def value(self, path: str):
        if path not in self.observed:
            self.observed[path] = self.sub.field(path)
        return self.observed[path]


class Book:
    def __init__(self, raws: list[dict], outcomes: dict):
        self.rows = []
        for raw in raws:
            sub = Submission.from_dict(raw)
            self.rows.append(_Row(sub, validate(sub), rate(sub).premium,
                                  outcomes[sub.submission_id].incurred,
                                  sub.coverage_requested.get("line_of_business") or "unknown",
                                  {}))


def apply_overrides(rules: list[GuidelineRule], overrides: dict) -> list[GuidelineRule]:
    """overrides: {rule_id: {"enabled": bool, "value": ..., "outcome": ...}}"""
    out = []
    for r in rules:
        o = overrides.get(r.rule_id, {})
        if o.get("enabled", True) is False:
            continue
        out.append(replace(r, **{k: o[k] for k in ("value", "outcome", "weight") if k in o}))
    return out


def _empty_bucket() -> dict:
    return {"submissions": 0, "accept": 0, "refer": 0, "decline": 0,
            "written_premium": 0.0, "incurred": 0.0, "declined_incurred": 0.0}


def _finish(b: dict) -> dict:
    n = b["submissions"] or 1
    lr = b["incurred"] / b["written_premium"] if b["written_premium"] else None
    return b | {
        "accept_rate": round(b["accept"] / n, 4),
        "refer_rate": round(b["refer"] / n, 4),
        "decline_rate": round(b["decline"] / n, 4),
        "written_premium": round(b["written_premium"], 0),
        "incurred": round(b["incurred"], 0),
        "declined_incurred": round(b["declined_incurred"], 0),
        "loss_ratio": round(lr, 4) if lr is not None else None,
        "combined_ratio": round(lr + EXPENSE_RATIO, 4) if lr is not None else None,
        "underwriting_profit": round(b["written_premium"] * (1 - EXPENSE_RATIO) - b["incurred"], 0),
    }


def simulate(book: Book, rules: list[GuidelineRule],
             refer_threshold: float = REFER_THRESHOLD) -> dict:
    total, by_line, hits = _empty_bucket(), {}, {}
    for row in book.rows:
        # same semantics as rules.evaluate_all, minus allocating non-matches
        results = [RuleResult(r.rule_id, r.description, True, obs, r.outcome, r.weight)
                   for r in rules
                   if _compare(obs := row.value(r.field), r.operator, r.value)]
        decision = decide_recommendation(results, row.validation, compute_risk_score(results),
                                         refer_threshold)
        for r in results:
            hits[r.rule_id] = hits.get(r.rule_id, 0) + 1
        for b in (total, by_line.setdefault(row.line, _empty_bucket())):
            b["submissions"] += 1
            b[decision] += 1
            if decision == "accept" and row.premium is not None:
                b["written_premium"] += row.premium
                b["incurred"] += row.incurred
            elif decision == "decline":
                b["declined_incurred"] += row.incurred
    return {
        "total": _finish(total),
        "by_line": {k: _finish(v) for k, v in sorted(by_line.items())},
        "rule_hits": dict(sorted(hits.items())),
    }
