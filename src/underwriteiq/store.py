"""SQL persistence: raw submissions, versioned rules, auditable assessments,
normalised rule hits / data-quality issues, pipeline run log and marts.

Stdlib sqlite3 only. The schema is plain ANSI-ish SQL (no SQLite-only types)
so it maps 1:1 onto Postgres; the marts are views, so analytics always read
the latest assessment per submission without a separate refresh job.

Layering (bronze -> silver -> gold):
    submissions, outcomes                    raw inputs, stored as received
    assessments, rule_hits, dq_issues        engine output, one row set per run
    v_latest_assessment, mart_*              read models for the dashboard
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .models import Assessment, GuidelineRule, Submission

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    submission_id TEXT PRIMARY KEY,
    applicant_json TEXT NOT NULL,
    coverage_requested_json TEXT NOT NULL,
    loss_runs_json TEXT NOT NULL DEFAULT '[]',
    property_details_json TEXT NOT NULL DEFAULT '{}',
    liability_details_json TEXT NOT NULL DEFAULT '{}',
    business_type TEXT,
    line_of_business TEXT,
    state TEXT,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    submission_id TEXT PRIMARY KEY REFERENCES submissions(submission_id),
    claim_count INTEGER NOT NULL,
    incurred REAL NOT NULL
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

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ruleset_version TEXT NOT NULL,
    rows_in INTEGER NOT NULL DEFAULT 0,
    rows_assessed INTEGER NOT NULL DEFAULT 0,
    rows_dq_flagged INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
    run_id INTEGER REFERENCES pipeline_runs(run_id),
    recommendation TEXT NOT NULL CHECK (recommendation IN ('accept','refer','decline')),
    risk_score REAL NOT NULL,
    premium REAL,
    loss_probability REAL,
    ruleset_version TEXT NOT NULL DEFAULT '',
    engine_version TEXT NOT NULL DEFAULT '',
    rule_results_json TEXT NOT NULL,
    rationale_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rule_hits (
    assessment_id INTEGER NOT NULL REFERENCES assessments(assessment_id),
    rule_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    weight INTEGER NOT NULL,
    observed TEXT,
    PRIMARY KEY (assessment_id, rule_id)
);

CREATE TABLE IF NOT EXISTS dq_issues (
    assessment_id INTEGER NOT NULL REFERENCES assessments(assessment_id),
    severity TEXT NOT NULL CHECK (severity IN ('missing','invalid','warning')),
    field TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assessments_submission ON assessments(submission_id, assessment_id);
CREATE INDEX IF NOT EXISTS idx_rule_hits_rule ON rule_hits(rule_id);
CREATE INDEX IF NOT EXISTS idx_dq_field ON dq_issues(field);

CREATE VIEW IF NOT EXISTS v_latest_assessment AS
SELECT a.*, s.business_type, s.line_of_business, s.state
FROM assessments a
JOIN submissions s USING (submission_id)
WHERE a.assessment_id = (
    SELECT MAX(assessment_id) FROM assessments x WHERE x.submission_id = a.submission_id
);

CREATE VIEW IF NOT EXISTS mart_decision_mix AS
SELECT COALESCE(line_of_business, 'unknown') AS line_of_business,
       recommendation,
       COUNT(*) AS submissions,
       ROUND(AVG(risk_score), 1) AS avg_risk_score,
       ROUND(SUM(COALESCE(premium, 0)), 0) AS indicated_premium
FROM v_latest_assessment
GROUP BY 1, 2;

CREATE VIEW IF NOT EXISTS mart_rule_hits AS
SELECT h.rule_id, r.description, r.outcome, COUNT(*) AS hits,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM v_latest_assessment), 2) AS hit_rate_pct
FROM rule_hits h
JOIN v_latest_assessment a USING (assessment_id)
LEFT JOIN guideline_rules r USING (rule_id)
GROUP BY h.rule_id
ORDER BY hits DESC;

CREATE VIEW IF NOT EXISTS mart_dq_issues AS
SELECT d.severity, d.field, COUNT(*) AS occurrences
FROM dq_issues d
JOIN v_latest_assessment a USING (assessment_id)
WHERE d.severity != 'warning'
GROUP BY 1, 2
ORDER BY occurrences DESC;

-- What the bound book (accepts) would have cost: joins next-year outcomes.
CREATE VIEW IF NOT EXISTS mart_bound_book AS
SELECT a.line_of_business,
       COUNT(*) AS policies,
       ROUND(SUM(a.premium), 0) AS written_premium,
       ROUND(SUM(o.incurred), 0) AS incurred_loss,
       ROUND(SUM(o.incurred) / NULLIF(SUM(a.premium), 0), 4) AS loss_ratio
FROM v_latest_assessment a
JOIN outcomes o USING (submission_id)
WHERE a.recommendation = 'accept' AND a.premium IS NOT NULL
GROUP BY 1;
"""


def _default_db_path() -> str:
    env = os.environ.get("UNDERWRITEIQ_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parents[2] / "data" / "underwriteiq.db")


def input_hash(sub: Submission) -> str:
    return hashlib.sha256(json.dumps(sub.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


class Store:
    """Thin SQLite data-access layer. Postgres swap-in point documented in README."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _default_db_path()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # API serves requests from a threadpool; writes are serialised by SQLite.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # -- submissions -------------------------------------------------
    def save_submission(self, sub: Submission, commit: bool = True) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO submissions (submission_id, applicant_json,"
            " coverage_requested_json, loss_runs_json, property_details_json,"
            " liability_details_json, business_type, line_of_business, state, input_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                sub.submission_id,
                json.dumps(sub.applicant),
                json.dumps(sub.coverage_requested),
                json.dumps(sub.loss_runs),
                json.dumps(sub.property_details),
                json.dumps(sub.liability_details),
                sub.applicant.get("business_type"),
                sub.coverage_requested.get("line_of_business"),
                sub.applicant.get("state"),
                input_hash(sub),
            ),
        )
        if commit:
            self.conn.commit()

    def get_submission(self, submission_id: str) -> Submission | None:
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

    def save_outcomes(self, outcomes) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO outcomes VALUES (?,?,?)",
                [(o.submission_id, o.claim_count, o.incurred) for o in outcomes],
            )

    # -- guideline rules ---------------------------------------------
    def save_rule(self, rule: GuidelineRule) -> None:
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

    def load_rules(self) -> list[GuidelineRule]:
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

    # -- pipeline runs -------------------------------------------------
    def start_run(self, source: str, ruleset_version: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO pipeline_runs (source, ruleset_version) VALUES (?,?)",
            (source, ruleset_version))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, rows_in: int, rows_assessed: int,
                   rows_dq_flagged: int, duration_ms: int) -> None:
        self.conn.execute(
            "UPDATE pipeline_runs SET rows_in=?, rows_assessed=?, rows_dq_flagged=?,"
            " duration_ms=?, finished_at=datetime('now') WHERE run_id=?",
            (rows_in, rows_assessed, rows_dq_flagged, duration_ms, run_id))
        self.conn.commit()

    # -- assessments ---------------------------------------------------
    def save_assessment(self, assessment: Assessment, run_id: int | None = None,
                        commit: bool = True) -> int:
        from . import __version__

        cur = self.conn.execute(
            "INSERT INTO assessments (submission_id, run_id, recommendation, risk_score,"
            " premium, loss_probability, ruleset_version, engine_version,"
            " rule_results_json, rationale_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                assessment.submission_id,
                run_id,
                assessment.recommendation,
                assessment.risk_score,
                assessment.premium,
                assessment.loss_probability,
                assessment.ruleset_version,
                __version__,
                json.dumps([vars(r) | {"observed_value": repr(r.observed_value)}
                            for r in assessment.rule_results], default=str),
                json.dumps(assessment.rationale_points),
            ),
        )
        aid = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO rule_hits VALUES (?,?,?,?,?)",
            [(aid, r.rule_id, r.outcome, r.weight, repr(r.observed_value))
             for r in assessment.matched_rules],
        )
        v = assessment.validation
        issues = ([("missing", f, f) for f in v.missing_fields]
                  + [("invalid", i.split(" ")[0], i) for i in v.invalid_fields]
                  + [("warning", "-", w) for w in v.warnings])
        self.conn.executemany(
            "INSERT INTO dq_issues VALUES (?,?,?,?)", [(aid, *i) for i in issues])
        if commit:
            self.conn.commit()
        return aid
