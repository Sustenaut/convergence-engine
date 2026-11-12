# convergence-engine: every deploy service walked read-only, its needs shown.
.PHONY: help venv functional lock sync-check check

help: ## Available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

venv: ## Create .venv from the hash-locked test requirements
	python3 -m venv .venv
	.venv/bin/pip install --quiet --require-hashes -r tests/requirements.lock
	.venv/bin/pip install --quiet --no-deps -e .

functional: ## The Gherkin suite, with its coverage in coverage/coverage.xml
	rm -rf coverage && mkdir coverage
	cd tests && PYTHONPATH=$(CURDIR)/src COVERAGE_RCFILE=$(CURDIR)/pyproject.toml COVERAGE_FILE=$(CURDIR)/coverage/.coverage \
	  ../.venv/bin/coverage run -m pytest -q $(PYTEST_ARGS)
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage combine -q
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage report
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage xml -q -o coverage/coverage.xml

lock: ## Pin tests/requirements.txt with hashes
	.venv/bin/pubkit lock

sync-check: ## Fail if pubkit's managed files drifted
	./pubkit sync --check

check: sync-check functional ## Everything CI runs
