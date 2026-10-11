"""Underwriter memo drafting: LLM writes, deterministic code verifies.

    draft = draft_memo(submission, assessment)        # Claude if ANTHROPIC_API_KEY, else template
    draft.checks                                      # every guardrail and whether it passed

The system prompt is the agent skill file (.agents/skills/underwriting/SKILL.md),
so the skill and the runtime can't drift apart. Claude returns a structured
`MemoDraft`; `verify()` then checks it against the Assessment:

  * decision_echo   - the memo restates the engine's recommendation, unchanged
  * no_fabrication  - every rule ID cited exists among the matched rules
  * coverage        - every matched rule is cited
  * missing_data    - every missing field is named in the broker requests

A draft that fails any check is discarded and the deterministic template is
returned instead, with the failure recorded. The LLM can explain the
decision; it can never change it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from .models import Assessment, Submission

MODEL_ID = "claude-opus-5-5"
SKILL_PATH = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "underwriting" / "SKILL.md"
RULE_ID = re.compile(r"\bGL-\d{3}\b")


class Driver(BaseModel):
    rule_id: str
    explanation: str


class MemoDraft(BaseModel):
    recommendation: str             # must echo the engine: accept | refer | decline
    summary: str                    # 3-6 sentences, plain English
    drivers: list[Driver]           # one per matched rule
    broker_requests: list[str]      # what to ask the broker for (missing data)
    reviewer_question: str          # the one question a human must answer
    ambiguity_flag: bool


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class MemoResult:
    memo: MemoDraft
    source: str                     # "claude" | "template" | "template (fallback)"
    checks: list[Check] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return all(c.passed for c in self.checks)


# -- deterministic template ---------------------------------------------------

def _fmt(v) -> str:
    if isinstance(v, bool) or v is None:
        return str(v).lower() if isinstance(v, bool) else "missing"
    if isinstance(v, (int, float)):
        return f"${v:,.0f}" if abs(v) >= 1_000 else f"{v:g}"
    return str(v).replace("_", " ")


def _near_threshold(assessment: Assessment, rules_by_id: dict) -> bool:
    for r in assessment.matched_rules:
        rule = rules_by_id.get(r.rule_id)
        if rule is None or not isinstance(rule.value, (int, float)) or isinstance(rule.value, bool):
            continue
        if isinstance(r.observed_value, (int, float)) and rule.value:
            if abs(r.observed_value - rule.value) / abs(rule.value) <= 0.10:
                return True
    return bool(assessment.validation.invalid_fields)


def template_memo(sub: Submission, a: Assessment, rules_by_id: dict | None = None) -> MemoDraft:
    rules_by_id = rules_by_id or {}
    app, cov = sub.applicant, sub.coverage_requested
    name = app.get("name") or sub.submission_id
    btype = (app.get("business_type") or "unspecified class").replace("_", " ")
    lob = (cov.get("line_of_business") or "unspecified line").replace("_", " ")
    limit = cov.get("limit")
    limit_txt = f"${limit:,.0f}" if isinstance(limit, (int, float)) else "an unspecified"
    matched = a.matched_rules

    parts = [f"{name} ({btype}) requests {limit_txt} limit {lob} coverage."]
    if matched:
        top = sorted(matched, key=lambda r: -r.weight)[:2]
        parts.append("Score drivers: " + "; ".join(
            f"{r.rule_id} {r.description[0].lower() + r.description[1:]} "
            f"(observed {_fmt(r.observed_value)})" for r in top) + ".")
    else:
        parts.append("No guideline rules matched; the risk sits inside appetite.")
    parts.append(f"Risk score {a.risk_score:.0f}/100; the engine recommends {a.recommendation.upper()}.")
    if a.premium is not None:
        parts.append(f"Indicated annual premium is ${a.premium:,.0f}.")
    if a.validation.missing_fields:
        parts.append(f"{len(a.validation.missing_fields)} required field(s) are missing, "
                     "so the file cannot be auto-accepted.")

    if a.recommendation == "decline":
        question = "Confirm the decline and notify the broker of the guideline basis."
    elif a.validation.missing_fields:
        question = "Once the broker supplies the missing data, does the risk still clear appetite?"
    elif matched:
        question = f"Is {matched[0].rule_id} mitigated by anything not captured in the submission?"
    else:
        question = "Any reason not to bind at the indicated premium?"

    return MemoDraft(
        recommendation=a.recommendation,
        summary=" ".join(parts),
        drivers=[Driver(rule_id=r.rule_id,
                        explanation=f"{r.description}: observed {_fmt(r.observed_value)}, "
                                    f"guideline outcome '{r.outcome}' (+{r.weight} risk).")
                 for r in matched],
        broker_requests=[f"Provide {f}" for f in a.validation.missing_fields]
                        + [f"Correct {i}" for i in a.validation.invalid_fields],
        reviewer_question=question,
        ambiguity_flag=_near_threshold(a, rules_by_id),
    )


# -- guardrails ---------------------------------------------------------------

def verify(memo: MemoDraft, a: Assessment) -> list[Check]:
    matched = {r.rule_id for r in a.matched_rules}
    text = " ".join([memo.summary, memo.reviewer_question,
                     *(d.explanation for d in memo.drivers), *memo.broker_requests])
    cited = set(RULE_ID.findall(text)) | {d.rule_id for d in memo.drivers}
    requests = " ".join(memo.broker_requests)
    missing = [f for f in a.validation.missing_fields if f not in requests]
    return [
        Check("decision_echo", memo.recommendation.strip().lower() == a.recommendation,
              f"memo={memo.recommendation!r} engine={a.recommendation!r}"),
        Check("no_fabrication", cited <= matched,
              f"uncited-but-claimed: {sorted(cited - matched)}" if cited - matched else ""),
        Check("coverage", matched <= cited,
              f"not cited: {sorted(matched - cited)}" if matched - cited else ""),
        Check("missing_data", not missing, f"not requested: {missing}" if missing else ""),
    ]


# -- Claude -------------------------------------------------------------------

def _claude_memo(sub: Submission, a: Assessment) -> MemoDraft:
    import anthropic

    payload = {
        "submission": sub.to_dict(),
        "assessment": {
            "recommendation": a.recommendation,
            "risk_score": a.risk_score,
            "premium": a.premium,
            "ruleset_version": a.ruleset_version,
            "matched_rules": [{"rule_id": r.rule_id, "description": r.description,
                               "observed": repr(r.observed_value), "outcome": r.outcome,
                               "weight": r.weight} for r in a.matched_rules],
            "missing_fields": a.validation.missing_fields,
            "invalid_fields": a.validation.invalid_fields,
        },
    }
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=MODEL_ID,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=SKILL_PATH.read_text(),
        messages=[{"role": "user", "content":
                   "Draft the plain-English summary and, if this is a referral, the referral "
                   "fields, for this deterministic assessment. Cite only rule IDs present in "
                   "matched_rules, cite every one of them, and list every missing field as a "
                   f"broker request.\n\n{payload}"}],
        output_format=MemoDraft,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"no structured memo (stop_reason={response.stop_reason})")
    return response.parsed_output


def draft_memo(sub: Submission, a: Assessment, rules_by_id: dict | None = None,
               use_llm: bool | None = None) -> MemoResult:
    if use_llm is None:
        use_llm = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if use_llm:
        try:
            memo = _claude_memo(sub, a)
            checks = verify(memo, a)
            if all(c.passed for c in checks):
                return MemoResult(memo, "claude", checks)
            failed = ", ".join(c.name for c in checks if not c.passed)
            fallback_note = Check("llm_draft_rejected", True, f"Claude draft failed: {failed}")
        except Exception as exc:  # network, auth, refusal: degrade, never block the desk
            fallback_note = Check("llm_unavailable", True, f"{type(exc).__name__}: {exc}"[:200])
        memo = template_memo(sub, a, rules_by_id)
        return MemoResult(memo, "template (fallback)", [*verify(memo, a), fallback_note])
    memo = template_memo(sub, a, rules_by_id)
    return MemoResult(memo, "template", verify(memo, a))
