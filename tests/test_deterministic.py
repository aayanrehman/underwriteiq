import json
from pathlib import Path

import pytest

from underwriteiq import (
    GuidelineRule, Store, Submission, assess_submission, evaluate_all,
    evaluate_rule, validate,
)
from underwriteiq.guidelines import DEFAULT_RULES
from underwriteiq.pipeline import load_submission

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def make_submission(**overrides) -> Submission:
    base = dict(
        submission_id="T-001",
        applicant={"business_type": "landscaping", "years_in_business": 7},
        coverage_requested={"line_of_business": "general_liability",
                            "limit": 1_000_000, "deductible": 5_000},
        loss_runs=[],
    )
    base.update(overrides)
    return Submission(**base)


# ---- validation ----

def test_clean_submission_is_complete():
    v = validate(make_submission())
    assert v.is_complete


def test_missing_required_field_is_flagged():
    sub = make_submission(applicant={"business_type": "retail"})  # no years_in_business
    v = validate(sub)
    assert "applicant.years_in_business" in v.missing_fields
    assert not v.is_complete


def test_non_numeric_limit_is_invalid():
    sub = make_submission(coverage_requested={
        "line_of_business": "gl", "limit": "a million", "deductible": 5000})
    v = validate(sub)
    assert any("not numeric" in i for i in v.invalid_fields)


def test_negative_years_is_out_of_range():
    sub = make_submission(applicant={"business_type": "retail",
                                     "years_in_business": -3})
    v = validate(sub)
    assert any("below minimum" in i for i in v.invalid_fields)


# ---- rules ----

def test_rule_gt_matches_high_limit():
    rule = GuidelineRule("R1", "limit high", "coverage_requested.limit", "gt",
                         500_000, "decline", 50)
    r = evaluate_rule(rule, make_submission())
    assert r.matched and r.outcome == "decline"


def test_rule_in_matches_appetite_list():
    rule = GuidelineRule("R2", "property refer", "coverage_requested.line_of_business",
                         "in", ["commercial_property"], "refer", 20)
    r = evaluate_rule(rule, make_submission())
    assert not r.matched  # landscaping GL is not in list


def test_missing_field_never_matches():
    rule = GuidelineRule("R3", "young biz", "applicant.years_in_business", "lt", 2,
                         "refer", 25)
    sub = make_submission(applicant={})
    r = evaluate_rule(rule, sub)
    assert not r.matched


# ---- scoring ----

def test_decline_wins_over_refer():
    results = evaluate_all(DEFAULT_RULES, Submission(
        submission_id="X",
        applicant={"business_type": "demolition", "years_in_business": 1},
        coverage_requested={"line_of_business": "general_liability",
                            "limit": 100_000, "deductible": 1_000},
    ))
    a = assess_submission("X", results, validate(Submission(
        submission_id="X",
        applicant={"business_type": "demolition", "years_in_business": 1},
        coverage_requested={"line_of_business": "general_liability",
                            "limit": 100_000, "deductible": 1_000},
    )))
    assert a.recommendation == "decline"


def test_risk_score_scales_and_caps():
    from underwriteiq import compute_risk_score
    weights = [10, 20]           # total 30 -> 30/60*100 = 50.0
    results = [type("R", (), {"matched": True, "weight": w})() for w in weights]
    assert compute_risk_score(results) == 50.0
    heavy = [type("R", (), {"matched": True, "weight": 40})() for _ in range(3)]  # 120 capped
    assert compute_risk_score(heavy) == 100.0


def test_incomplete_data_forces_refer_even_without_rule_match():
    sub = Submission(submission_id="Y", applicant={}, coverage_requested={})
    a = assess_submission("Y", [], validate(sub))
    assert a.recommendation == "refer"


# ---- end-to-end on synthetic samples ----

@pytest.mark.parametrize("filename,expected", [
    ("submission_bluebird_clean.json", "accept"),
    ("submission_redrock_decline.json", "decline"),
    ("submission_cedar_incomplete.json", "refer"),
])
def test_sample_files_end_to_end(filename, expected, tmp_path):
    sub = load_submission(str(SAMPLES / filename))
    store = Store(db_path=str(tmp_path / "t.db"))
    for r in DEFAULT_RULES:
        store.save_rule(r)
    validation = validate(sub)
    results = evaluate_all(store.load_rules(), sub)
    assessment = assess_submission(sub.submission_id, results, validation)
    store.save_assessment(assessment)
    assert assessment.recommendation == expected
    # persisted?
    rows = store.conn.execute(
        "SELECT recommendation FROM assessments WHERE submission_id=?",
        (sub.submission_id,)).fetchall()
    assert rows and rows[0]["recommendation"] == expected


def test_store_roundtrips_submission(tmp_path):
    store = Store(db_path=str(tmp_path / "s.db"))
    sub = make_submission()
    store.save_submission(sub)
    back = store.get_submission("T-001")
    assert back.applicant == sub.applicant
    assert back.coverage_requested == sub.coverage_requested
