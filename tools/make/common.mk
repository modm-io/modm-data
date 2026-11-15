# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @Utils Utilities \1010

log/%:
	@mkdir -p $@


.DEFAULT_GOAL := help
.PHONY: help
## @General Generate this help output.
help:
	@python3 tools/scripts/makefile_help.py ${MAKEFILE_LIST}

.PHONY: update-ext/%
update-ext/%:
	@(cd $(@:update-%=%) && git fetch && git reset --hard origin/main)

# ============================= Python Management =============================
.PHONY: pip-upgrade-freeze
# Update the tools/requirements.txt file for maintenance.
pip-upgrade-freeze: clean-venv
	@python3 -m venv .venv
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
## Remove the virtual environment
clean-venv:
	@rm -rf .venv


.PHONY: format
## Format the Python source code
format:
	@ruff format src
	@ruff check --fix src
	@ruff format src

# =============================== Documentation ===============================
# The pages of the homepage, see https://github.com/modm-ext/modm-data-tools
MODM_DATA_TOOLS ?= ext/modm-data-tools

.PHONY: build-homepage
## Generate the entire homepage to /docs.
build-homepage:
	@rm -rf docs/data.modm.dev/docs/
	@mkdir -p docs/data.modm.dev/docs/
	@python3 tools/scripts/synchronize_docs.py
	@pdoc --mermaid -o docs/src/api -t docs/pdoc modm_data
	@python3 -m modm_data.pinout --output docs/src/pinout/ --templates $(MODM_DATA_TOOLS)/templates/pinout/
	@python3 -m modm_data.header2svd.stmicro --all --output docs/src/svd/
	@python3 -m modm_data.svd2variants --all --input docs/src/svd/ --output docs/src/variants/
	@python3 -m modm_data.html2pinout --output docs/src/boards/nucleo.json --pinout docs/src/pinout
	@for page in shared svd variants boards pinout tools; do cp -R $(MODM_DATA_TOOLS)/web/$$page docs/src/ || exit 1; done
	@(cd docs && mkdocs build)


.PHONY: serve-api-docs
## Serve the API docs locally for development.
serve-api-docs:
	@pdoc --mermaid modm_data

### @Tests Testing \1009
# ================================== Testing ==================================
ext/test/regression/:
	@git clone --depth=1 https://github.com/modm-ext/modm-data-test-docs.git $@

.PHONY: run-regression-tests
## @Tests Convert some PDF pages and check against their known HTML.
run-regression-tests: ext/test/regression/
	@test/convert_html.sh
	@git diff --exit-code -- test/data/html

