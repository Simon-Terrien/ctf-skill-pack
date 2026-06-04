UV ?= uv
VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
RUNTIME_DIR ?= runtime
TRACE_DIR ?= .ctfrt/traces
UV_CACHE_DIR ?= /tmp/uv-cache

LLM_NAME ?= llm-static-test
LLM_CATEGORY ?= reverse
LLM_TEXT ?= noise CTF{llm_static_test} end
LLM_FLAG_FORMAT ?= CTF\{[^}]+\}
LLM_ARTIFACT ?=

.PHONY: help venv sync test smoke compile clean solve-local submit trace-show trace-export llm-drive biobrain-run cms-bootstrap

help:
	@printf '%s\n' \
		'Targets:' \
		'  make venv        - create the local virtual environment with uv' \
		'  make sync        - install the project and dependencies with uv sync' \
		'  make test        - run the runtime smoke and integration tests' \
		'  make smoke       - run runtime/tests/smoke_runtime.py' \
		'  make compile     - byte-compile runtime code and tests' \
		'  make solve-local  - run ctfrt.cli solve-local (set ARGS=...)' \
		'  make submit      - run ctfrt.cli submit (set ARGS=...)' \
		'  make llm-drive   - run a controlled BioBrain-backed solve-local flow' \
		'  make biobrain-run - run biobrain CLI (set ARGS=...)' \
		'  make cms-bootstrap - bootstrap the CMS SQLite database' \
		'  make trace-show   - print a trace (set CHALLENGE_ID=...)' \
		'  make trace-export - export a trace (set CHALLENGE_ID=...)' \
		'  make clean       - remove local caches and the virtualenv'

venv:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) venv $(VENV)

sync:
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) sync

test: smoke
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python runtime/tests/test_engine_weld.py
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python runtime/tests/test_cms_cag.py

smoke: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python runtime/tests/smoke_runtime.py

compile: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m compileall -q runtime/ctfrt runtime/tests

solve-local: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m ctfrt.cli solve-local $(ARGS)

submit: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m ctfrt.cli submit $(ARGS)

llm-drive: sync
	@tmpdir=""; \
	artifact="$(LLM_ARTIFACT)"; \
	trap 'if [ -n "$$tmpdir" ]; then rm -rf "$$tmpdir"; fi' EXIT INT TERM; \
	if [ -z "$$artifact" ]; then \
		tmpdir=$$(mktemp -d /tmp/ctf-llm.XXXXXX); \
		artifact="$$tmpdir/$(LLM_NAME).txt"; \
		printf '%s\n' "$(LLM_TEXT)" > "$$artifact"; \
	fi; \
	CTF_AGENT_ENGINE=biobrain CTF_MEMORY_QUERY=none CTF_TRACE_DIR=$(TRACE_DIR) \
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m ctfrt.cli solve-local \
		--name "$(LLM_NAME)" \
		--category "$(LLM_CATEGORY)" \
		--artifact "$$artifact" \
		--flag-format "$(LLM_FLAG_FORMAT)"

biobrain-run: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m biobrain $(ARGS)

cms-bootstrap: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python cms_runtime/scripts/bootstrap_db.py $(ARGS)

trace-show: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m ctfrt.cli show-trace --challenge-id $(CHALLENGE_ID) $(TRACE_ARGS)

trace-export: sync
	UV_CACHE_DIR=$(UV_CACHE_DIR) $(UV) run python -m ctfrt.cli export-trace --challenge-id $(CHALLENGE_ID) $(TRACE_ARGS)

clean:
	rm -rf $(VENV) .pytest_cache runtime/.pytest_cache
	find runtime -type d -name __pycache__ -prune -exec rm -rf {} +
