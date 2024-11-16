# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @Utils Utilities \1000

log/%:
	@mkdir -p $@


.DEFAULT_GOAL := help
.PHONY: help
## @General Generate this help output.
help:
	@python3 tools/scripts/makefile_help.py ${MAKEFILE_LIST}


# ============================= Python Management =============================
.PHONY: pip-upgrade-freeze
# Update the tools/requirements.txt file for maintenance.
pip-upgrade-freeze:
	@.venv/bin/pip install --upgrade --upgrade-strategy=eager -e ".[all]"
	@.venv/bin/pip freeze --exclude modm_data > tools/requirements.txt

.PHONY: pip-install-frozen
# Install the frozen dependencies from tools/requirements.txt.
pip-install-frozen:
	@.venv/bin/pip install -r tools/requirements.txt -e ".[all]"


.PHONY: venv
## Create the Python virtual environment and install the dependencies.
venv:
	@python3 -m venv .venv
	$(MAKE) pip-install-frozen

.PHONY: clean-venv
# Remove the virtual environment
clean-venv:
	@rm -rf .venv


# =============================== Documentation ===============================
.PHONY: build-homepage
## Generate the entire homepage to /docs.
build-homepage:
	@rm -rf docs/data.modm.io/docs/
	@mkdir -p docs/data.modm.io/docs/
	@python3 tools/scripts/synchronize_docs.py
	@pdoc --mermaid -o docs/src/api -t docs/pdoc modm_data
	@(cd docs && mkdocs build)


.PHONY: serve-api-docs
## Serve the API docs locally for development.
serve-api-docs:
	@pdoc --mermaid modm_data


# ================================== Testing ==================================
ext/test/regression/:
	@git clone --depth=1 git@github.com:modm-ext/modm-data-test-docs.git $@

.PHONY: run-regression-tests
## Convert some PDF pages and check against their known HTML.
run-regression-tests: ext/test/regression/
	@test/convert_html.sh
	@git diff --exit-code -- test/data/html

