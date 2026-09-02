# ASSAY — common developer tasks.
#
# The one thing worth knowing: the regression suite runs skills as SUBPROCESSES,
# which import `assay_core` and `skills.*`. Without an editable install (or
# PYTHONPATH) those children fail to import and the suite reports ~50 failures
# that have nothing to do with the code. `make install` is the fix; `make test`
# works either way because tests/conftest.py exports PYTHONPATH itself.

PYTHON ?= python
PYTEST ?= $(PYTHON) -m pytest

.DEFAULT_GOAL := help
.PHONY: help install test test-all lint serve clean clean-runs

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Editable install (pip deps only; xtb/mopac/openbabel come from conda)
	$(PYTHON) -m pip install -e .
	$(PYTHON) -m pip install pytest pytest-cov
	@echo
	@echo "Backends are NOT pip-installable. For xtb / mopac / openbabel:"
	@echo "  conda env create -f environment.yml && conda activate assay"
	@echo "  (or: pixi install)"

test:  ## Fast regression suite (skips >30s cases)
	$(PYTEST) tests/ -q -m "not slow"

test-all:  ## Full suite including slow cases
	$(PYTEST) tests/ -q

lint:  ## Skill contract lint: SKILL.md + run.py spine + registry sync + dep DAG
	$(PYTHON) tools/lint_skills.py --all

serve:  ## Start the stdio MCP server
	$(PYTHON) -m mcp_server.server

clean:  ## Remove caches and build artifacts
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache build dist *.egg-info

clean-runs:  ## Delete calculation scratch dropped in the repo root
	@rm -f ./*.out ./*.json ./*.xyz ./*.cube ./*.molden ./*_input_configs.yaml
	@echo "root scratch cleared (use --outdir to keep runs out of the repo)"
