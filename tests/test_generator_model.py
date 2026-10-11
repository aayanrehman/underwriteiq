import numpy as np

from underwriteiq.generator import generate_book
from underwriteiq.model import LossModel, auc, train_on_book


def test_book_is_deterministic_per_seed():
    a, oa = generate_book(200, seed=11)
    b, ob = generate_book(200, seed=11)
    c, _ = generate_book(200, seed=12)
    assert a == b and oa == ob
    assert a != c


def test_book_has_injected_defects_near_target_rate(small_book):
    book, _ = small_book
    def clean(r):
        a, c = r["applicant"], r["coverage_requested"]
        return ("years_in_business" in a and "business_type" in a and "deductible" in c
                and isinstance(c["limit"], int) and a["annual_revenue"] > 0)
    rate = sum(not clean(r) for r in book) / len(book)
    assert 0.02 < rate < 0.09


def test_auc_matches_known_values():
    y = np.array([0, 0, 1, 1])
    assert auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5


def test_model_learns_signal_and_roundtrips(tmp_path):
    model = train_on_book(*generate_book(5_000, seed=42))
    assert model.metrics["test_auc"] > 0.6           # clearly better than chance, held out
    path = tmp_path / "m.json"
    model.save(path)
    back = LossModel.load(path)
    X = np.random.default_rng(0).normal(size=(5, len(model.mean))) + np.array(model.mean)
    assert np.allclose(model.predict(X), back.predict(X))
