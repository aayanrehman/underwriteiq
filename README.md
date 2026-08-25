# UnderwriteIQ

An AI-assisted insurance underwriting review copilot: takes a structured
(synthetic) commercial or personal lines submission and produces a
deterministic risk assessment — validation report, guideline-rule matches,
0–100 risk score, and an accept / refer / decline recommendation — for a
**human underwriter to review**.

## Why it exists

I hold the AINS (Associate in Insurance) designation and interned at
Voya Financial, where I became interested in data governance and how
financial-services decisions get documented and audited. This project is my
way of connecting that domain background with hands-on Python/SQL work:
modeling submissions, guideline rules, and assessments in a real schema, and
keeping decision logic deterministic and testable rather than buried inside
an LLM prompt.

**Honest scope:** this has never been used on a real underwriting decision,
a real carrier's guidelines, or real client data. Everything in `data/` is
synthetic/fictional. It is a portfolio project demonstrating architecture,
not production software.

## Working v1 (tested locally)

- **Deterministic assessment engine** (`src/underwriteiq/`):
  - Data validation with required-field / type / range checks and warnings
  - Guideline-rule matching over 9 operators (`gt`, `gte`, `lt`, `lte`, `eq`,
    `neq`, `in`, `not_in`) against any submission field
  - Risk scoring math (matched-rule weights scaled to 0–100) and a fixed
    decision cascade: decline rules win → incomplete data forces refer →
    refer rules → score threshold → accept
  - 13 unit tests covering all of the above, including end-to-end runs on
    three synthetic sample submissions
- **SQL layer**: SQLite store with `submissions`, `guideline_rules`,
  `assessments` tables (schema + indexes), seedable ruleset
  (`guidelines.py`), read-back round-trips covered by tests
- **CLI pipeline**: `python -m underwriteiq.pipeline data/samples/<file>.json`
  loads → validates → scores → persists → prints a cited rationale
- **REST API** (FastAPI): `POST /assess` accepting submission JSON,
  returning recommendation + matched rules + validation report; seeds the
  default ruleset on first call
- **Agent skill layer** (`.agents/skills/underwriting/SKILL.md`): instructions
  for an LLM assistant whose job is strictly *drafting* — plain-English
  summaries and referral memos from the deterministic Assessment output.
  The agent cannot override the decision; that boundary is stated as a hard
  rule in the skill file.

## Architecture principle

Two layers, deliberately separated:

| Layer | Location | LLM? | Tested? |
|---|---|---|---|
| Validation, rule matching, scoring, SQL | `src/underwriteiq/` | No | Yes (pytest) |
| Summary/referral drafting, ambiguity flagging | `.agents/skills/underwriting/` | Yes | Judgment-bound by skill rules |

Every recommendation is reproducible without any network call. The LLM never
decides; it explains what the deterministic engine already decided.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v

# CLI on a synthetic sample
python -m underwriteiq.pipeline data/samples/submission_bluebird_clean.json

# REST API
uvicorn underwriteiq.api:app --port 8000
curl -s -X POST localhost:8000/assess \
  -H "Content-Type: application/json" \
  -d @data/samples/submission_redrock_decline.json
```

## Tech stack (what actually ships)

Python 3.11+, FastAPI, Pydantic v2, uvicorn, pytest, sqlite3 (stdlib).
CI: GitHub Actions running tests on push/PR across Python 3.11/3.12.
The vendored Aspire rating engine is reference code only (see notices below)
and is not imported at runtime.

## Attribution

`vendor/aspire/` contains [nemmons/aspire](https://github.com/nemmons/aspire)
(A Simple Python Insurance Rating Engine, MIT © Nate Emmons), vendored
unmodified as a reference implementation of rating-manual concepts. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Future extensions (not built yet)

- PostgreSQL backend behind the same Store interface (schema already maps 1:1)
- ACORD-format ingestion of broker submission documents
- Actual LLM integration executing the agent skill against Assessment JSON
- Rating (premium calculation) building on the vendored aspire patterns
- Near-threshold sensitivity reporting surfaced through the API

## License

MIT. Synthetic data only throughout.
