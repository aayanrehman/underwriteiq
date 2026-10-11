from underwriteiq import Store
from underwriteiq.guidelines import DEFAULT_RULES, ruleset_version
from underwriteiq.pipeline import ingest_batch


def test_ruleset_version_is_order_independent_and_content_sensitive():
    from dataclasses import replace
    v = ruleset_version(DEFAULT_RULES)
    assert v == ruleset_version(list(reversed(DEFAULT_RULES)))
    assert v != ruleset_version([replace(DEFAULT_RULES[0], weight=99), *DEFAULT_RULES[1:]])


def test_batch_ingest_logs_run_and_populates_marts(tmp_path, small_book):
    raws, outcomes = small_book
    store = Store(str(tmp_path / "b.db"))
    stats = ingest_batch([*raws, {"no": "id"}, "junk"], store, outcomes=outcomes)

    assert stats["rows_assessed"] == len(raws)
    assert stats["rows_rejected"] == 2
    run = store.query("SELECT * FROM pipeline_runs")[0]
    assert run["rows_assessed"] == len(raws) and run["finished_at"]

    versions = store.query("SELECT DISTINCT ruleset_version FROM assessments")
    assert versions == [{"ruleset_version": ruleset_version(DEFAULT_RULES)}]

    mix = store.query("SELECT SUM(submissions) AS n FROM mart_decision_mix")[0]["n"]
    assert mix == len(raws)
    assert store.query("SELECT COUNT(*) AS n FROM mart_bound_book")[0]["n"] >= 1
    assert store.query("SELECT * FROM mart_dq_issues")  # injected defects surface


def test_reassessment_keeps_history_but_marts_read_latest(tmp_path, small_book):
    raws, outcomes = small_book
    store = Store(str(tmp_path / "h.db"))
    ingest_batch(raws[:50], store)
    ingest_batch(raws[:50], store)
    assert store.query("SELECT COUNT(*) AS n FROM assessments")[0]["n"] == 100
    assert store.query("SELECT COUNT(*) AS n FROM v_latest_assessment")[0]["n"] == 50
