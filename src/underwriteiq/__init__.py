"""UnderwriteIQ — deterministic insurance underwriting engine and simulator.

The LLM drafting layer (memo.py) consumes this package's Assessment objects
and is verified against them; nothing in the decision path calls an LLM.
"""
__version__ = "0.2.0"

from .models import Assessment, GuidelineRule, RuleResult, Submission, ValidationResult
from .rules import evaluate_all, evaluate_rule
from .scoring import assess_submission, compute_risk_score, decide_recommendation
from .store import Store
from .validation import validate

__all__ = [
    "Assessment", "GuidelineRule", "RuleResult", "Submission", "ValidationResult",
    "evaluate_all", "evaluate_rule", "assess_submission", "compute_risk_score",
    "decide_recommendation", "Store", "validate",
]
