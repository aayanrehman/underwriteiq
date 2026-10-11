#!/usr/bin/env python3
"""End-to-end pipeline: load submission JSON -> validate -> rule match ->
score -> rate -> persist -> print assessment. Deterministic; no LLM calls.

Usage:
    python -m underwriteiq.pipeline data/samples/submission_bluebird_clean.json

Optional env:
    UNDERWRITEIQ_DB   path to SQLite file (default data/underwriteiq.db)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# allow running as a plain script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from underwriteiq import (  # noqa: E402
    Store,
    Submission,
    assess_submission,
    evaluate_all,
    validate,
)
from underwriteiq.guidelines import DEFAULT_RULES, ruleset_version  # noqa: E402
from underwriteiq.models import Assessment, GuidelineRule  # noqa: E402
from underwriteiq.rating import rate  # noqa: E402


def load_submission(path: str) -> Submission:
    return Submission.from_dict(json.loads(Path(path).read_text()))


def assess(sub: Submission, rules: list[GuidelineRule], model=None,
           version: str | None = None) -> Assessment:
    """The whole deterministic engine for one submission. Pure: no I/O."""
    validation = validate(sub)
    assessment = assess_submission(sub.submission_id, evaluate_all(rules, sub), validation)
    assessment.ruleset_version = version or ruleset_version(rules)
    assessment.premium = rate(sub).premium
    if model is not None:
        assessment.loss_probability = model.predict_one(sub)
    return assessment


def ensure_rules(store: Store) -> list[GuidelineRule]:
    rules = store.load_rules()
    if not rules:
        for r in DEFAULT_RULES:
            store.save_rule(r)
        rules = list(DEFAULT_RULES)
    return rules


def run_assessment(sub: Submission, store: Store, model=None) -> Assessment:
    assessment = assess(sub, ensure_rules(store), model)
    store.save_submission(sub)
    store.save_assessment(assessment)
    return assessment


def ingest_batch(raws: list[dict], store: Store, model=None, source: str = "batch",
                 outcomes=None) -> dict:
    """Batch-load raw submissions in one transaction and log the run.

    Malformed records (no submission_id, not an object) are counted and
    skipped rather than aborting the batch.
    """
    rules = ensure_rules(store)
    version = ruleset_version(rules)
    run_id = store.start_run(source, version)
    t0 = time.perf_counter()
    assessed = dq_flagged = rejected = 0
    with store.conn:  # single transaction: all-or-nothing
        for raw in raws:
            if not isinstance(raw, dict) or not raw.get("submission_id"):
                rejected += 1
                continue
            sub = Submission.from_dict(raw)
            a = assess(sub, rules, model, version)
            store.save_submission(sub, commit=False)
            store.save_assessment(a, run_id=run_id, commit=False)
            assessed += 1
            dq_flagged += not a.validation.is_complete
    if outcomes:
        store.save_outcomes(outcomes.values())
    ms = int((time.perf_counter() - t0) * 1000)
    store.finish_run(run_id, len(raws), assessed, dq_flagged, ms)
    return {"run_id": run_id, "rows_in": len(raws), "rows_assessed": assessed,
            "rows_rejected": rejected, "rows_dq_flagged": dq_flagged,
            "ruleset_version": version, "duration_ms": ms}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 1
    sub = load_submission(argv[1])
    store = Store()
    assessment = run_assessment(sub, store)

    print(f"Submission : {assessment.submission_id}")
    print(f"Ruleset    : {assessment.ruleset_version}")
    print(f"Validation : complete={assessment.validation.is_complete} "
          f"missing={assessment.validation.missing_fields} "
          f"invalid={assessment.validation.invalid_fields}")
    for w in assessment.validation.warnings:
        print(f"  warning  : {w}")
    print(f"Risk score : {assessment.risk_score}")
    if assessment.premium is not None:
        print(f"Premium    : ${assessment.premium:,.0f}")
    print(f"Recommendation: {assessment.recommendation.upper()}")
    print("Rationale:")
    for point in assessment.rationale_points:
        print(f"  - {point}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
