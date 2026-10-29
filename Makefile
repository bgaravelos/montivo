PY ?= python3.12
VENV = .venv

.PHONY: dev test clean ui web

dev: $(VENV)/bin/montivo

$(VENV)/bin/montivo: pyproject.toml constraints.txt
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e ".[dev]" -c constraints.txt
	@echo "ok: $(VENV)/bin/montivo (export MODEL_API_KEY=... before running)"

test: dev
	$(VENV)/bin/pytest -q

ui: dev
	$(VENV)/bin/montivo serve

# rebuild the committed UI bundle (montivo/server/static) after editing web/src; needs Node 18+
web:
	cd web && npm ci && npm run build

clean:
	rm -rf $(VENV) build *.egg-info
