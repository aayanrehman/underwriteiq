"""UnderwriteIQ — deterministic insurance underwriting review engine.

The LLM agent layer lives separately (.agents/skills/underwriting/) and
consumes this package's Assessment objects; nothing here calls an LLM.
"""
__version__ = "0.1.0"

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
