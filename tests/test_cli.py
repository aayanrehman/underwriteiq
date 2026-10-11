from underwriteiq.cli import _parse_sets, main


def test_parse_sets_builds_override_dict():
    assert _parse_sets(["GL-003.value=10000000", "GL-002.enabled=false"]) == {
        "GL-003": {"value": 10_000_000}, "GL-002": {"enabled": False}}


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("UNDERWRITEIQ_DB", str(tmp_path / "cli.db"))
    monkeypatch.chdir(tmp_path)
    assert main(["generate", "--n", "50", "--out", "book.jsonl"]) == 0
    assert len((tmp_path / "book.jsonl").read_text().splitlines()) == 50
    assert main(["ingest", "--n", "300"]) == 0
    assert main(["report"]) == 0
    assert main(["simulate", "--n", "300", "--set", "GL-003.enabled=false"]) == 0
    assert main(["train", "--n", "300"]) == 0
    out = capsys.readouterr().out
    assert "mart_bound_book" in out and "loss_ratio" in out and "test_auc" in out
