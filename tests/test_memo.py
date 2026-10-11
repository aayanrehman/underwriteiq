from pathlib import Path

import pytest

from underwriteiq import memo
from underwriteiq.guidelines import DEFAULT_RULES
from underwriteiq.memo import Driver, draft_memo, template_memo, verify
from underwriteiq.pipeline import assess, load_submission

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "data" / "samples").glob("*.json"))
RULES = {r.rule_id: r for r in DEFAULT_RULES}


def case(path):
    sub = load_submission(str(path))
    return sub, assess(sub, DEFAULT_RULES)


@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem)
def test_template_memo_passes_every_guardrail(path):
    sub, a = case(path)
    result = draft_memo(sub, a, RULES, use_llm=False)
    assert result.source == "template"
    assert result.verified, [c for c in result.checks if not c.passed]


def test_guardrails_catch_a_hallucinating_llm():
    sub, a = case(SAMPLES[-1])  # redrock: decline, many matched rules
    bad = template_memo(sub, a, RULES)
    bad.recommendation = "accept"                                   # flips the decision
    bad.drivers = [Driver(rule_id="GL-999", explanation="invented")]  # fabricated, drops real ones
    failed = {c.name for c in verify(bad, a) if not c.passed}
    assert failed == {"decision_echo", "no_fabrication", "coverage"}


def test_rejected_llm_draft_falls_back_to_template(monkeypatch):
    sub, a = case(SAMPLES[-1])
    def fake_claude(s, asmt):
        m = template_memo(s, asmt)
        m.recommendation = "refer"
        return m
    monkeypatch.setattr(memo, "_claude_memo", fake_claude)
    result = draft_memo(sub, a, RULES, use_llm=True)
    assert result.source == "template (fallback)"
    assert result.memo.recommendation == a.recommendation
    assert any(c.name == "llm_draft_rejected" for c in result.checks)


def test_llm_outage_degrades_instead_of_failing(monkeypatch):
    sub, a = case(SAMPLES[0])
    monkeypatch.setattr(memo, "_claude_memo", lambda *_: (_ for _ in ()).throw(ConnectionError("down")))
    result = draft_memo(sub, a, RULES, use_llm=True)
    assert result.source == "template (fallback)" and result.verified


def test_missing_fields_become_broker_requests():
    sub, a = case(next(p for p in SAMPLES if "cedar" in p.name))
    m = template_memo(sub, a, RULES)
    for f in a.validation.missing_fields:
        assert any(f in req for req in m.broker_requests)
