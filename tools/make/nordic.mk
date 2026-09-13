# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @Nordic Vendor Nordic Semiconductor \103

# =============================== Input Sources ===============================
ext/nordic/nrfx/:
	@git clone --depth=1 https://github.com/NordicSemiconductor/nrfx.git $@

.PHONY: clone-sources-nordic
## Clone all Nordic related repositories into /ext/nordic.
clone-sources-nordic: ext/nordic/nrfx/

.PHONY: update-sources-nordic
## Update all Nordic related repositories to the latest version.
update-sources-nordic:
	@(cd ext/nordic/nrfx && git fetch --depth=1 && git reset --hard origin/master)

# The Nordic documentation cannot be downloaded automatically, see the
# documentation of modm_data.nrfx.pinout for how to update the pinout data.
.PHONY: convert-nordic-pinout
## Convert the pin assignment chapters of the product specifications saved as
## /ext/nordic/pinout/ps_{product}-pin.html and merge them into the nrfx pinout
## JSON file.
convert-nordic-pinout:
	@python3 -c "from modm_data.nrfx import write_pinout_json; print(write_pinout_json('ext/nordic/pinout'))"
