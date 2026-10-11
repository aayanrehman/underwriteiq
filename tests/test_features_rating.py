from underwriteiq import Submission
from underwriteiq.rating import _deductible_credit, _ilf, rate


def sub(**over):
    base = dict(
        submission_id="R-1",
        applicant={"business_type": "landscaping", "years_in_business": 7,
                   "annual_revenue": 1_000_000, "employees": 10},
        coverage_requested={"line_of_business": "general_liability",
                            "limit": 1_000_000, "deductible": 1_000},
        loss_runs=[],
    )
    base.update(over)
    return Submission(**base)


def test_derived_counts_only_paid_losses_in_window():
    s = sub(loss_runs=[
        {"year": 2026, "loss_amount": 40_000},
        {"year": 2025, "loss_amount": 0},          # no-claim year: not a loss
        {"year": 2024, "loss_amount": 120_000},
        {"year": 2019, "loss_amount": 999_999},    # outside 3-year window
    ])
    d = s.derived
    assert d["loss_count_3y"] == 2
    assert d["total_incurred_3y"] == 160_000
    assert d["max_single_loss"] == 120_000
    assert d["limit_to_revenue"] == 1.0


def test_rules_can_address_derived_fields():
    assert sub().field("derived.loss_count_3y") == 0


def test_base_case_premium_is_base_rate_times_exposure():
    w = rate(sub())
    # 2.10 per $1k * $1M revenue, all other factors 1.0
    assert w.premium == 2_100.0
    assert w.steps[-1] == ("Indicated premium", 2_100.0)


def test_ilf_interpolates_and_clamps():
    assert _ilf(1_000_000) == 1.0
    assert _ilf(1_500_000) == 1.14
    assert _ilf(50_000_000) == 2.05
    assert _ilf(100_000) == 0.80


def test_deductible_credit_caps_at_20_percent():
    assert _deductible_credit(1_000) == 1.0
    assert _deductible_credit(4_000) == 0.95
    assert _deductible_credit(10_000_000) == 0.80


def test_unratable_submission_returns_no_premium():
    s = sub(coverage_requested={"line_of_business": "general_liability",
                                "limit": "a million", "deductible": 1_000})
    assert rate(s).premium is None
    assert rate(sub(applicant={"business_type": "unknown_class", "years_in_business": 3,
                               "annual_revenue": 1e6})).premium is None
