"""Loss-propensity model: P(at least one claim next year).

L2-regularised logistic regression fitted with Newton-Raphson (IRLS) in
plain numpy, so the whole model is ~10 coefficients you can read in a JSON
file. It is an *advisory signal* shown next to the rule engine's decision;
the decision cascade in scoring.py never reads it.

    python -m underwriteiq.cli train      # fit on the synthetic book, write data/model.json
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Submission
from .rating import CLASSES

FEATURES = ["log_revenue", "years_in_business", "loss_count_3y", "log_incurred_3y",
            "class_hazard", "log_limit", "is_property", "unsprinklered", "new_venture"]


def featurize(sub: Submission) -> list[float] | None:
    """Feature vector, or None when required inputs are missing/malformed."""
    a, c, d = sub.applicant, sub.coverage_requested, sub.derived
    years, limit = a.get("years_in_business"), c.get("limit")
    if (d["log_revenue"] is None or a.get("business_type") not in CLASSES
            or not isinstance(years, (int, float)) or not isinstance(limit, (int, float))
            or limit <= 0):
        return None
    return [
        d["log_revenue"],
        min(float(years), 30.0),
        float(d["loss_count_3y"]),
        math.log1p(d["total_incurred_3y"]),
        CLASSES[a["business_type"]][0],
        math.log10(limit),
        float(c.get("line_of_business") == "commercial_property"),
        float(sub.property_details.get("sprinklered") is False),
        float(years < 3),
    ]


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """ROC AUC via the Mann-Whitney rank statistic (ties get average rank)."""
    order = p.argsort()
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    for value in np.unique(p):  # average ranks over ties
        mask = p == value
        ranks[mask] = ranks[mask].mean()
    pos = y == 1
    n_pos, n_neg = pos.sum(), (~pos).sum()
    return float((ranks[pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


@dataclass
class LossModel:
    mean: list[float]
    std: list[float]
    coef: list[float]        # intercept first
    metrics: dict

    @classmethod
    def fit(cls, X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 25) -> LossModel:
        mean, std = X.mean(0), X.std(0)
        std[std == 0] = 1.0
        Z = np.hstack([np.ones((len(X), 1)), (X - mean) / std])
        w = np.zeros(Z.shape[1])
        reg = l2 * np.eye(len(w))
        reg[0, 0] = 0  # don't shrink the intercept
        for _ in range(iters):
            p = 1 / (1 + np.exp(-Z @ w))
            grad = Z.T @ (p - y) + reg @ w
            hess = (Z * (p * (1 - p))[:, None]).T @ Z + reg
            step = np.linalg.solve(hess, grad)
            w -= step
            if np.abs(step).max() < 1e-8:
                break
        return cls(mean.tolist(), std.tolist(), w.tolist(), {})

    def predict(self, X: np.ndarray) -> np.ndarray:
        Z = (X - np.array(self.mean)) / np.array(self.std)
        return 1 / (1 + np.exp(-(Z @ np.array(self.coef[1:]) + self.coef[0])))

    def predict_one(self, sub: Submission) -> float | None:
        x = featurize(sub)
        return None if x is None else round(float(self.predict(np.array([x]))[0]), 4)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "features": FEATURES, "mean": self.mean, "std": self.std,
            "coef": self.coef, "metrics": self.metrics}, indent=2))

    @classmethod
    def load(cls, path: str | Path) -> LossModel:
        raw = json.loads(Path(path).read_text())
        return cls(raw["mean"], raw["std"], raw["coef"], raw.get("metrics", {}))


def train_on_book(book: list[dict], outcomes: dict, test_frac: float = 0.2) -> LossModel:
    rows, labels = [], []
    for raw in book:
        x = featurize(Submission.from_dict(raw))
        if x is not None:
            rows.append(x)
            labels.append(1.0 if outcomes[raw["submission_id"]].claim_count > 0 else 0.0)
    X, y = np.array(rows), np.array(labels)
    cut = int(len(X) * (1 - test_frac))  # book order is random, so a head/tail split is fair
    model = LossModel.fit(X[:cut], y[:cut])

    p_test = model.predict(X[cut:])
    y_test = y[cut:]
    brier = float(np.mean((p_test - y_test) ** 2))
    model.metrics = {
        "train_rows": cut, "test_rows": len(X) - cut,
        "base_rate": round(float(y.mean()), 4),
        "test_auc": round(auc(y_test, p_test), 4),
        "test_brier": round(brier, 4),
        "brier_skill": round(1 - brier / float(np.mean((y_test.mean() - y_test) ** 2)), 4),
        "coefficients": dict(zip(["intercept", *FEATURES], [round(c, 4) for c in model.coef], strict=True)),
    }
    return model
