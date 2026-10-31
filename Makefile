# the first Python 3.12+ on the PATH (3.12 is the tested one); `make dev PY=/path/to/python3.12` picks another
PY ?= $(shell for p in python3.12 python3.13 python3; do \
	command -v $$p >/dev/null 2>&1 && $$p -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null \
	&& { echo $$p; break; }; done)
VENV = .venv
# .env (copied from .env.example) is read by `make ui` only; the montivo CLI reads the environment as it is
ENV = set -a; [ -f .env ] && . ./.env; set +a;

.PHONY: dev test clean ui web sample check

dev: check $(VENV)/bin/montivo

# what a clone needs before pip: Python 3.12+, ffmpeg and ffprobe with the libx264 encoder
check:
	@test -n "$(PY)" || { echo "Montivo needs Python 3.12 or newer on the PATH."; \
	  echo "  macOS:  brew install python@3.12"; \
	  echo "  Debian / Ubuntu:  sudo apt install python3.12 python3.12-venv   (or: make dev PY=/path/to/python3.12)"; exit 1; }
	@command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 || { \
	  echo "Montivo renders with ffmpeg: ffmpeg and ffprobe must be on the PATH."; \
	  echo "  macOS:  brew install ffmpeg"; \
	  echo "  Debian / Ubuntu:  sudo apt install ffmpeg"; exit 1; }
	@ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264 || { \
	  echo "This ffmpeg has no libx264 encoder (Montivo writes H.264): install the full build,"; \
	  echo "  macOS:  brew install ffmpeg      Debian / Ubuntu:  sudo apt install ffmpeg"; exit 1; }

$(VENV)/bin/montivo: pyproject.toml constraints.txt
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip >/dev/null
	$(VENV)/bin/pip install -e ".[dev]" -c constraints.txt
	@echo "ok: $(VENV)/bin/montivo (Python $$($(VENV)/bin/python -c 'import platform; print(platform.python_version())'))"

test: dev
	$(VENV)/bin/pytest -q

# a finished sample run, checked and unpacked into runs/ and footage/ (no key needed to open it):
#   make sample                  runs/015, the run the demo video shows
#   make sample SAMPLE=all       every sample (SAMPLE=017 for one)
#   make sample FROM=~/Downloads the zips downloaded by hand
sample: dev
	$(VENV)/bin/python -m montivo.server.sample fetch $(if $(SAMPLE),--sample $(SAMPLE)) $(if $(FROM),--from "$(FROM)")

ui: dev
	@$(ENV) $(VENV)/bin/montivo serve

# rebuild the committed UI bundle (montivo/server/static) after editing web/src; needs Node 18+
web:
	cd web && npm ci && npm run build

clean:
	rm -rf $(VENV) build *.egg-info
