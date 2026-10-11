.PHONY: install test lint eval serve demo docker

install:
	python -m venv .venv && .venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest -q --cov=underwriteiq

lint:
	.venv/bin/ruff check src tests evals

eval:
	.venv/bin/python evals/memo_eval.py --n 300

serve:
	.venv/bin/uwiq serve

demo:  ## re-record docs/demo.gif (needs playwright + ffmpeg)
	.venv/bin/python scripts/record_demo.py

docker:
	docker build -t underwriteiq . && docker run --rm -p 8000:8000 underwriteiq
