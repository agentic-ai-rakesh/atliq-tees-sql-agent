# AtliQ Tees SQL Agent
# make setup      create .venv (Python 3.11+), install requirements.txt, create .env, build the database
# make db         (re)build data/atliq_tshirts.db from source/*.csv
# make mysql-sql  regenerate source/db_creation_atliq_t_shirts.sql from source/*.csv
# make backend    FastAPI on http://127.0.0.1:8000 (docs at /docs)
# make frontend   Streamlit on http://localhost:8501
# make run        both together (Ctrl+C stops both)
# make test       run the tests (no API key or network needed)
# make check      lint + format check + tests + requirements check (pip check)
# make verify     clean-room check: fresh copy, make setup, make check; report in reports/

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
VENV := $(ROOT)/.venv/bin
PYTHON ?= python3.11
BACKEND_PORT ?= 8000
FRONTEND_PORT ?= 8501
export TSHIRT_AGENT_API_URL ?= http://127.0.0.1:$(BACKEND_PORT)

.PHONY: help setup env db mysql-sql backend frontend run test lint format check verify clean

help:
	@grep -E '^# (make |AtliQ)' $(MAKEFILE_LIST) | sed 's/^# //'

setup: env
	cd "$(ROOT)" && if command -v uv >/dev/null 2>&1; then \
		uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt; \
	else \
		$(PYTHON) -m venv .venv && .venv/bin/python -m pip install --upgrade pip && \
		.venv/bin/python -m pip install -r requirements.txt; \
	fi
	$(MAKE) -C "$(ROOT)" db
	@echo "Setup done. Add your GOOGLE_API_KEY (or OPENAI_API_KEY + LLM_PROVIDER=openai) to .env, then: make run"

env:
	@cd "$(ROOT)" && if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example"; fi

db:
	cd "$(ROOT)" && "$(VENV)/python" -m tshirt_agent.seed_data

mysql-sql:
	cd "$(ROOT)" && "$(VENV)/python" -m tshirt_agent.seed_data --mysql-sql

backend:
	cd "$(ROOT)" && "$(VENV)/python" -m uvicorn api.main:app --reload --host 127.0.0.1 --port $(BACKEND_PORT)

frontend:
	cd "$(ROOT)" && "$(VENV)/python" -m streamlit run frontend/app.py --server.port $(FRONTEND_PORT)

run:
	cd "$(ROOT)" && \
	"$(VENV)/python" -m uvicorn api.main:app --host 127.0.0.1 --port $(BACKEND_PORT) & \
	BACKEND_PID=$$!; \
	trap 'kill $$BACKEND_PID 2>/dev/null; wait $$BACKEND_PID 2>/dev/null' EXIT INT TERM; \
	sleep 2; \
	cd "$(ROOT)" && "$(VENV)/python" -m streamlit run frontend/app.py --server.port $(FRONTEND_PORT)

test:
	cd "$(ROOT)" && "$(VENV)/python" -m pytest -q

lint:
	cd "$(ROOT)" && "$(VENV)/ruff" check . && "$(VENV)/ruff" format --check .

format:
	cd "$(ROOT)" && "$(VENV)/ruff" check --fix . && "$(VENV)/ruff" format .

check: lint
	cd "$(ROOT)" && "$(VENV)/python" -m pytest -q -p no:cacheprovider
	cd "$(ROOT)" && if command -v uv >/dev/null 2>&1; then uv pip check --python .venv/bin/python; \
		else .venv/bin/python -m pip check; fi

# Copies the files git would publish (tracked + new, not ignored) into an empty folder,
# then runs make setup and make check there, exactly as a new user would.
VERIFY_DIR ?= $(or $(TMPDIR),/tmp)/atliq_verify
verify:
	rm -rf "$(VERIFY_DIR)" && mkdir -p "$(VERIFY_DIR)" "$(ROOT)/reports"
	cd "$(ROOT)" && git ls-files -co --exclude-standard -z | xargs -0 tar -cf - | tar -xf - -C "$(VERIFY_DIR)"
	cd "$(VERIFY_DIR)" && $(MAKE) -s setup && $(MAKE) -s lint && \
		.venv/bin/python -m pytest -v -p no:cacheprovider | tee "$(ROOT)/reports/test_results.txt" | tail -1 && \
		(command -v uv >/dev/null 2>&1 && uv pip check --python .venv/bin/python || .venv/bin/python -m pip check) && \
		.venv/bin/python --version
	@echo "Clean-room check passed. Test report: reports/test_results.txt"

clean:
	cd "$(ROOT)" && rm -rf data logs reports .pytest_cache .ruff_cache && find . -name __pycache__ -prune -exec rm -rf {} +
