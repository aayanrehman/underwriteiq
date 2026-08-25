"""SQL persistence for submissions, guideline rules, and assessments.

SQLite by default (local dev); PostgreSQL via DATABASE_URL when provided.
Plain sqlite3/psycopg-free approach: uses SQLAlchemy Core-style raw SQL
with only stdlib sqlite3 so the deterministic layer has zero heavy deps.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    applicant_json TEXT NOT NULL,
    coverage_requested_json TEXT NOT NULL,
    loss_runs_json TEXT NOT NULL DEFAULT '[]',
    property_details_json TEXT NOT NULL DEFAULT '{}',
    liability_details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS guideline_rules (
    rule_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    field TEXT NOT NULL,
    operator TEXT NOT NULL,
    value_json TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accept','refer','decline','surcharge')),
    weight INTEGER NOT NULL DEFAULT 1,
    section TEXT NOT NULL DEFAULT 'general'
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
    recommendation TEXT NOT NULL CHECK (recommendation IN ('accept','refer','decline')),
    risk_score REAL NOT NULL,
    rule_results_json TEXT NOT NULL,
    rationale_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_assessments_submission ON assessments(submission_id);
"""


def _default_db_path() -> str:
    env = os.environ.get("UNDERWRITEIQ_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "underwriteiq.db")


class Store:
    """Thin SQLite data-access layer. Postgres swap-in point documented in README."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _default_db_path()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- submissions -------------------------------------------------
    def save_submission(self, sub) -> None:
        from .models import Submission  # local import to avoid cycles

        assert isinstance(sub, Submission)
        self.conn.execute(
            "INSERT OR REPLACE INTO submissions VALUES (?,?,?,?,?,?,datetime('now'))",
            (
                sub.submission_id,
                json.dumps(sub.applicant),
                json.dumps(sub.coverage_requested),
                json.dumps(sub.loss_runs),
                json.dumps(sub.property_details),
                json.dumps(sub.liability_details),
            ),
        )
        self.conn.commit()

    def get_submission(self, submission_id: str):
        from .models import Submission

        row = self.conn.execute(
            "SELECT * FROM submissions WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if row is None:
            return None
        return Submission(
            submission_id=row["submission_id"],
            applicant=json.loads(row["applicant_json"]),
            coverage_requested=json.loads(row["coverage_requested_json"]),
            loss_runs=json.loads(row["loss_runs_json"]),
            property_details=json.loads(row["property_details_json"]),
            liability_details=json.loads(row["liability_details_json"]),
        )

    # -- guideline rules ---------------------------------------------
    def save_rule(self, rule) -> None:
        from .models import GuidelineRule

        assert isinstance(rule, GuidelineRule)
        self.conn.execute(
            "INSERT OR REPLACE INTO guideline_rules VALUES (?,?,?,?,?,?,?,?)",
            (
                rule.rule_id,
                rule.description,
                rule.field,
                rule.operator,
                json.dumps(rule.value),
                rule.outcome,
                rule.weight,
                rule.section,
            ),
        )
        self.conn.commit()

    def load_rules(self) -> list:
        from .models import GuidelineRule

        rows = self.conn.execute("SELECT * FROM guideline_rules ORDER BY rule_id").fetchall()
        return [
            GuidelineRule(
                rule_id=r["rule_id"],
                description=r["description"],
                field=r["field"],
                operator=r["operator"],
                value=json.loads(r["value_json"]),
                outcome=r["outcome"],
                weight=r["weight"],
                section=r["section"],
            )
            for r in rows
        ]

    # -- assessments ---------------------------------------------------
    def save_assessment(self, assessment) -> int:
        cur = self.conn.execute(
            "INSERT INTO assessments (submission_id, recommendation, risk_score,"
            " rule_results_json, rationale_json) VALUES (?,?,?,?,?)",
            (
                assessment.submission_id,
                assessment.recommendation,
                assessment.risk_score,
                json.dumps([vars(r) | {"observed_value": repr(r.observed_value)}
                            for r in assessment.rule_results], default=str),
                json.dumps(assessment.rationale_points),
            ),
        )
        self.conn.commit()
        return cur.lastrowid
