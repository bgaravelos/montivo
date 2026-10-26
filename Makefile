PY ?= python3.12
VENV = .venv

.PHONY: dev test clean

dev: $(VENV)/bin/montivo

$(VENV)/bin/montivo: pyproject.toml
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e ".[dev]"
	@echo "ok: $(VENV)/bin/montivo (export MODEL_API_KEY=... before running)"

test: dev
	$(VENV)/bin/pytest -q

clean:
	rm -rf $(VENV) build *.egg-info
