.PHONY: run tunnel test smoke

PORT ?= 7860

run:
	python -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT) --workers 1

tunnel:
	./run.sh

test:
	pytest -v

smoke:
	python scripts/smoke_test.py

