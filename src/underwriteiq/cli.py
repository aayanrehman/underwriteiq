"""`uwiq` command line.

    uwiq generate --n 5000 --out data/book.jsonl     synthetic book + hidden outcomes
    uwiq ingest   --n 5000                           batch-load into SQLite, log the run
    uwiq train                                       fit the loss model, print the model card
    uwiq simulate --set GL-003.value=10000000        what-if vs baseline over the book
    uwiq report                                      portfolio marts straight from SQL
    uwiq serve                                       API + web UI on :8000
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .generator import generate_book
from .guidelines import DEFAULT_RULES
from .model import train_on_book
from .pipeline import ingest_batch
from .simulate import Book, apply_overrides, simulate
from .store import Store


def _table(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0])
    widths = [max(len(str(c)), *(len(str(r[c])) for r in rows)) for c in cols]
    line = lambda vals: "  ".join(str(v).ljust(w) for v, w in zip(vals, widths, strict=True))  # noqa: E731
    return "\n".join([line(cols), line("-" * w for w in widths), *(line(r.values()) for r in rows)])


def _parse_sets(pairs: list[str]) -> dict:
    """GL-003.value=10000000  GL-002.enabled=false  ->  override dict"""
    out: dict = {}
    for pair in pairs:
        key, raw = pair.split("=", 1)
        rule_id, attr = key.split(".", 1)
        out.setdefault(rule_id, {})[attr] = json.loads(raw)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="uwiq", description="UnderwriteIQ command line")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "ingest", "train", "simulate"):
        s = sub.add_parser(name)
        s.add_argument("--n", type=int, default=5000)
        s.add_argument("--seed", type=int, default=42)
    sub.choices["generate"].add_argument("--out", default="data/book.jsonl")
    sub.choices["simulate"].add_argument("--set", nargs="*", default=[], metavar="RULE.attr=json")
    sub.choices["simulate"].add_argument("--refer-threshold", type=float, default=40)
    sub.add_parser("report")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    if args.cmd == "generate":
        book, outcomes = generate_book(args.n, args.seed)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("".join(json.dumps(r) + "\n" for r in book))
        out.with_name(out.stem + "_outcomes.jsonl").write_text(
            "".join(json.dumps(asdict(o)) + "\n" for o in outcomes.values()))
        print(f"wrote {len(book)} submissions -> {out}")
    elif args.cmd == "ingest":
        book, outcomes = generate_book(args.n, args.seed)
        model = train_on_book(book, outcomes)
        print(json.dumps(ingest_batch(book, Store(), model, f"synthetic:n={args.n},seed={args.seed}",
                                      outcomes), indent=2))
    elif args.cmd == "train":
        book, outcomes = generate_book(args.n, args.seed)
        model = train_on_book(book, outcomes)
        Path("data").mkdir(exist_ok=True)
        model.save("data/model.json")
        print(json.dumps(model.metrics, indent=2))
    elif args.cmd == "simulate":
        book = Book(*generate_book(args.n, args.seed))
        base = simulate(book, DEFAULT_RULES)["total"]
        scen = simulate(book, apply_overrides(DEFAULT_RULES, _parse_sets(args.set)),
                        args.refer_threshold)["total"]
        keys = ["accept_rate", "refer_rate", "decline_rate", "written_premium",
                "loss_ratio", "combined_ratio", "underwriting_profit"]
        print(_table([{"metric": k, "baseline": base[k], "scenario": scen[k]} for k in keys]))
    elif args.cmd == "report":
        store = Store()
        for view in ("mart_decision_mix", "mart_bound_book", "mart_rule_hits", "mart_dq_issues"):
            print(f"\n== {view}\n{_table(store.query(f'SELECT * FROM {view}'))}")
    elif args.cmd == "serve":
        import uvicorn

        uvicorn.run("underwriteiq.api:app", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
