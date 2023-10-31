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

.PHONY: build-docs
build-docs:
	@rm -rf docs/data.modm.io/docs/
	@mkdir -p docs/data.modm.io/docs/
	@python3 tools/scripts/synchronize_docs.py
	@pdoc --mermaid -o docs/src/api -t docs/pdoc modm_data
	@(cd docs && mkdocs build)
