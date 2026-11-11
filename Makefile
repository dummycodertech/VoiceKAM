.PHONY: run tunnel test eval eval-live smoke

PORT ?= 7860
PYTHON ?= python
EVAL_N ?= 1
EVAL_LIVE_N ?= 2

run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT) --workers 1

tunnel:
	./run.sh

test:
	$(PYTHON) -m pytest -v

eval:
	$(PYTHON) scripts/run_eval.py --offline --n $(EVAL_N)

eval-live:
	$(PYTHON) scripts/run_eval.py --live --n $(EVAL_LIVE_N)

smoke:
	$(PYTHON) scripts/smoke_test.py

