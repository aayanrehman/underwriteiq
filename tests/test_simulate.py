from underwriteiq import Submission
from underwriteiq.guidelines import DEFAULT_RULES
from underwriteiq.pipeline import assess
from underwriteiq.simulate import Book, apply_overrides, simulate


def test_fast_path_matches_engine_decision_for_every_submission(small_book):
    raws, outcomes = small_book
    result = simulate(Book(raws, outcomes), DEFAULT_RULES)["total"]
    expected = {"accept": 0, "refer": 0, "decline": 0}
    for raw in raws:
        expected[assess(Submission.from_dict(raw), DEFAULT_RULES).recommendation] += 1
    assert {k: result[k] for k in expected} == expected


def test_disabling_decline_rules_removes_rule_based_declines(small_book):
    book = Book(*small_book)
    no_decline = apply_overrides(DEFAULT_RULES, {"GL-003": {"enabled": False},
                                                 "GL-005": {"enabled": False}})
    assert simulate(book, no_decline)["total"]["decline"] == 0


def test_overrides_do_not_mutate_default_rules():
    before = [r.value for r in DEFAULT_RULES]
    apply_overrides(DEFAULT_RULES, {"GL-003": {"value": 1}})
    assert [r.value for r in DEFAULT_RULES] == before


def test_loss_ratio_is_incurred_over_bound_premium(small_book):
    t = simulate(Book(*small_book), DEFAULT_RULES)["total"]
    assert abs(t["loss_ratio"] - t["incurred"] / t["written_premium"]) < 1e-3
