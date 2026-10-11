"""REST API + web UI.

Run:  uwiq serve            (or: uvicorn underwriteiq.api:app --port 8000)
Open: http://localhost:8000

On startup the synthetic book is generated (seeded), the loss model is
fitted, and, if the database has never been loaded, the book is batch
ingested so the portfolio marts have data. Endpoints are `async` so SQLite
access is serialised on the event loop; the heavy lifting is ~20ms of CPU.

Env:
    UNDERWRITEIQ_DB          SQLite path (default data/underwriteiq.db)
    UNDERWRITEIQ_BOOK_SIZE   synthetic book size (default 5000)
    ANTHROPIC_API_KEY        enables Claude memo drafting (template otherwise)
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import Store, Submission, __version__
from .generator import generate_book
from .guidelines import ruleset_version
from .memo import MODEL_ID, draft_memo
from .model import train_on_book
from .pipeline import assess, ensure_rules, ingest_batch, run_assessment
from .rating import BASE_RATES, CLASSES, rate
from .scoring import MAX_SCORE_WEIGHTS, REFER_THRESHOLD
from .simulate import Book, apply_overrides, simulate

WEB = Path(__file__).parent / "web"
SAMPLES = Path(__file__).resolve().parents[2] / "data" / "samples"
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    n = int(os.environ.get("UNDERWRITEIQ_BOOK_SIZE", "5000"))
    raws, outcomes = generate_book(n, seed=42)
    model = train_on_book(raws, outcomes)
    store = Store()
    rules = ensure_rules(store)
    if not store.query("SELECT 1 FROM pipeline_runs WHERE finished_at IS NOT NULL LIMIT 1"):
        ingest_batch(raws, store, model, f"synthetic:n={n},seed=42", outcomes)
    book = Book(raws, outcomes)
    state.update(raws={r["submission_id"]: r for r in raws}, book=book, model=model,
                 store=store, rules=rules, baseline=simulate(book, rules))
    yield
    store.conn.close()


app = FastAPI(title="UnderwriteIQ", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


class SubmissionIn(BaseModel):
    submission_id: str
    applicant: dict[str, Any] = {}
    coverage_requested: dict[str, Any] = {}
    loss_runs: list[dict[str, Any]] = []
    property_details: dict[str, Any] = {}
    liability_details: dict[str, Any] = {}

    def to_submission(self) -> Submission:
        return Submission.from_dict(self.model_dump())


class SimulateIn(BaseModel):
    overrides: dict[str, dict[str, Any]] = {}
    refer_threshold: float = REFER_THRESHOLD


def _assessment_view(sub: Submission) -> dict:
    rules = state["rules"]
    a = assess(sub, rules, state["model"])
    worksheet = rate(sub)
    by_id = {r.rule_id: r for r in a.rule_results}
    return {
        "submission_id": a.submission_id,
        "recommendation": a.recommendation,
        "risk_score": a.risk_score,
        "premium": a.premium,
        "loss_probability": a.loss_probability,
        "ruleset_version": a.ruleset_version,
        "engine_version": __version__,
        "rules": [{
            "rule_id": r.rule_id, "description": r.description, "section": r.section,
            "field": r.field, "operator": r.operator, "threshold": r.value,
            "outcome": r.outcome, "weight": r.weight,
            "matched": by_id[r.rule_id].matched,
            "observed": by_id[r.rule_id].observed_value,
        } for r in rules],
        "validation": {
            "complete": a.validation.is_complete,
            "missing_fields": a.validation.missing_fields,
            "invalid_fields": a.validation.invalid_fields,
            "warnings": a.validation.warnings,
        },
        "rating": {"premium": worksheet.premium, "reason": worksheet.reason,
                   "steps": [{"label": k, "value": v} for k, v in worksheet.steps]},
        "derived": sub.derived,
        "rationale_points": a.rationale_points,
    }


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "book_size": len(state["raws"])}


@app.get("/api/meta")
async def meta() -> dict:
    rules = state["rules"]
    return {
        "version": __version__,
        "ruleset_version": ruleset_version(rules),
        "rules": [r.__dict__ for r in rules],
        "classes": {k: {"hazard": h, "median_revenue": m} for k, (h, m) in CLASSES.items()},
        "lines": list(BASE_RATES),
        "refer_threshold": REFER_THRESHOLD,
        "max_score_weights": MAX_SCORE_WEIGHTS,
        "model": state["model"].metrics,
        "llm_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "llm_model": MODEL_ID,
        "book_size": len(state["raws"]),
    }


@app.get("/api/queue")
async def queue() -> list[dict]:
    """Inbox for the desk: the three hand-written samples plus a spread of book risks."""
    items = [json.loads(p.read_text()) for p in sorted(SAMPLES.glob("*.json"))]
    want = {"accept": 3, "refer": 3, "decline": 2}
    for raw in state["raws"].values():
        if not any(want.values()):
            break
        sub = Submission.from_dict(raw)
        rec = assess(sub, state["rules"]).recommendation
        if want.get(rec, 0) and sub.applicant.get("business_type") not in {
                i["applicant"].get("business_type") for i in items[3:]}:
            items.append(raw)
            want[rec] -= 1
    return items


@app.post("/api/assess")
async def api_assess(payload: SubmissionIn) -> dict:
    return _assessment_view(payload.to_submission())


@app.post("/api/memo")
async def api_memo(payload: SubmissionIn) -> dict:
    sub = payload.to_submission()
    a = assess(sub, state["rules"], state["model"])
    result = draft_memo(sub, a, {r.rule_id: r for r in state["rules"]})
    return {"source": result.source, "verified": result.verified,
            "memo": result.memo.model_dump(),
            "checks": [c.__dict__ for c in result.checks]}


@app.post("/api/simulate")
async def api_simulate(payload: SimulateIn) -> dict:
    rules = apply_overrides(state["rules"], payload.overrides)
    return {"baseline": state["baseline"],
            "scenario": simulate(state["book"], rules, payload.refer_threshold),
            "ruleset_version": ruleset_version(rules)}


@app.get("/api/portfolio")
async def portfolio() -> dict:
    q = state["store"].query
    return {
        "kpis": q("""
            SELECT COUNT(*) AS submissions,
                   SUM(recommendation='accept') AS accepted,
                   SUM(recommendation='refer') AS referred,
                   SUM(recommendation='decline') AS declined,
                   ROUND(AVG(risk_score), 1) AS avg_risk_score,
                   ROUND(AVG(loss_probability), 4) AS avg_loss_probability
            FROM v_latest_assessment""")[0],
        "decision_mix": q("SELECT * FROM mart_decision_mix ORDER BY line_of_business, recommendation"),
        "bound_book": q("SELECT * FROM mart_bound_book ORDER BY written_premium DESC"),
        "rule_hits": q("SELECT * FROM mart_rule_hits"),
        "dq_issues": q("SELECT * FROM mart_dq_issues LIMIT 10"),
        "runs": q("SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT 5"),
    }


# -- v0.1 contract, kept for existing clients: assess and persist ------------

@app.post("/assess")
async def assess_legacy(payload: SubmissionIn) -> dict:
    try:
        a = run_assessment(payload.to_submission(), state["store"], state["model"])
    except Exception as exc:  # narrow surface for a demo API
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "submission_id": a.submission_id,
        "recommendation": a.recommendation,
        "risk_score": a.risk_score,
        "premium": a.premium,
        "ruleset_version": a.ruleset_version,
        "matched_rules": [
            {"rule_id": r.rule_id, "outcome": r.outcome, "observed": repr(r.observed_value)}
            for r in a.matched_rules
        ],
        "validation": {
            "complete": a.validation.is_complete,
            "missing_fields": a.validation.missing_fields,
            "invalid_fields": a.validation.invalid_fields,
            "warnings": a.validation.warnings,
        },
        "rationale_points": a.rationale_points,
    }
