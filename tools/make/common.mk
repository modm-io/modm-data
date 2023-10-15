# Copyright (c) 2023, Niklas Hauser
#
# This file is part of the modm-data project.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# -----------------------------------------------------------------------------

.PHONY: nothing
nothing:

log/%:
	@mkdir -p $@


.PHONY: install-pip
install-pip:
	@pip3 install -e .

.PHONY: install-pip-docs
install-pip-docs:
	@pip3 install -e ".[docs]"

.PHONY: build-docs
build-docs:
	@rm -rf docs/data.modm.io/docs/
	@pdoc modm_data --mermaid -o docs/data.modm.io/docs/
	@echo "data.modm.io" > docs/data.modm.io/docs/CNAME
	@touch docs/data.modm.io/docs/.nojekyll
