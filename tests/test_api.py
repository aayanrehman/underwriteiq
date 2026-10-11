import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / "data" / "samples"
                     / "submission_redrock_decline.json").read_text())


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("UNDERWRITEIQ_DB", str(tmp_path_factory.mktemp("api") / "api.db"))
    mp.setenv("UNDERWRITEIQ_BOOK_SIZE", "400")
    mp.delenv("ANTHROPIC_API_KEY", raising=False)
    from underwriteiq.api import app
    with TestClient(app) as c:
        yield c
    mp.undo()


def test_ui_and_health(client):
    assert "UnderwriteIQ" in client.get("/").text
    assert client.get("/api/health").json()["book_size"] == 400


def test_assess_returns_full_trace(client):
    r = client.post("/api/assess", json=SAMPLE).json()
    assert r["recommendation"] == "decline"
    assert len(r["rules"]) == 8 and r["rating"]["steps"]
    assert r["ruleset_version"].startswith("rs-")


def test_memo_is_verified(client):
    r = client.post("/api/memo", json=SAMPLE).json()
    assert r["verified"] and r["memo"]["recommendation"] == "decline"


def test_simulate_changes_ruleset_version(client):
    r = client.post("/api/simulate", json={"overrides": {"GL-003": {"enabled": False}}}).json()
    assert r["scenario"]["total"]["submissions"] == 400
    assert r["scenario"]["total"]["decline"] <= r["baseline"]["total"]["decline"]


def test_portfolio_reads_marts(client):
    p = client.get("/api/portfolio").json()
    assert p["kpis"]["submissions"] == 400 and p["runs"]


def test_legacy_assess_contract_persists(client):
    r = client.post("/assess", json=SAMPLE).json()
    assert r["recommendation"] == "decline"
    assert {"submission_id", "risk_score", "matched_rules", "validation"} <= set(r)
