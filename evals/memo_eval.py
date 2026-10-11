"""Faithfulness eval for the memo layer.

Drafts a memo for N submissions sampled across the synthetic book and scores
each draft with the same guardrails the API enforces. Template mode runs in
CI and must score 100%; `--llm` scores raw Claude drafts *before* fallback,
which is the number that tells you whether the prompt is any good.

    python evals/memo_eval.py --n 200            # deterministic template
    python evals/memo_eval.py --n 50 --llm       # Claude (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from underwriteiq import Submission
from underwriteiq.generator import generate_book
from underwriteiq.guidelines import DEFAULT_RULES
from underwriteiq.memo import _claude_memo, template_memo, verify
from underwriteiq.pipeline import assess


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--llm", action="store_true")
    args = p.parse_args()

    raws, _ = generate_book(args.n, seed=args.seed)
    rules = {r.rule_id: r for r in DEFAULT_RULES}
    passed, failures, decisions = Counter(), [], Counter()
    for raw in raws:
        sub = Submission.from_dict(raw)
        a = assess(sub, DEFAULT_RULES)
        decisions[a.recommendation] += 1
        try:
            draft = _claude_memo(sub, a) if args.llm else template_memo(sub, a, rules)
        except Exception as exc:
            failures.append((sub.submission_id, "error", str(exc)[:120]))
            continue
        for c in verify(draft, a):
            passed[c.name] += c.passed
            if not c.passed:
                failures.append((sub.submission_id, c.name, c.detail))

    n = len(raws)
    print(f"memo eval: {n} submissions, backend={'claude' if args.llm else 'template'}, "
          f"mix={dict(decisions)}\n")
    print("| check | pass rate |\n|---|---|")
    for name in ("decision_echo", "no_fabrication", "coverage", "missing_data"):
        print(f"| {name} | {passed[name] / n:.1%} |")
    for f in failures[:10]:
        print("FAIL", *f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
