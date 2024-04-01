# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @ARM Vendor ARM \100

# =============================== Input Sources ===============================
ext/arm/cmsis/:
	@git clone --depth=1 git@github.com:modm-ext/cmsis-5-partial.git $@

.PHONY: clone-sources-arm
## Clone all ARM related repositories into /ext/arm.
clone-sources-arm: ext/arm/cmsis/

.PHONY: update-sources-arm
## Update all ARM related repositories to the latest version.
update-sources-arm:
	@(cd ext/arm/cmsis && git pull) &
	@wait

