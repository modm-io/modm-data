# Copyright (c) 2023, Niklas Hauser
#
# This file is part of the modm-data project.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# -----------------------------------------------------------------------------

# =============================== Input Sources ===============================
ext/arm/cmsis/:
	@git clone --depth=1 git@github.com:modm-ext/cmsis-5-partial.git $@

.PHONY: clone-sources-arm
clone-sources-arm: ext/arm/cmsis/

.PHONY: update-sources-arm
update-sources-arm:
	@(cd ext/arm/cmsis && git pull) &
	@wait

