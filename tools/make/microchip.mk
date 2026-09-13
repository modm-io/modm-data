# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @Microchip Vendor Microchip \102

# =============================== Input Sources ===============================
ext/microchip/avr/:
	@python3 -m modm_data.dl.microchip --directory $@ --download avr --patch

ext/microchip/sam/:
	@python3 -m modm_data.dl.microchip --directory $@ --download sam

.PHONY: download-sources-microchip
## Download the AVR and SAM device packs into /ext/microchip.
download-sources-microchip: ext/microchip/avr/ ext/microchip/sam/

.PHONY: update-sources-microchip
## Update the AVR and SAM device packs to the latest version.
update-sources-microchip:
	@rm -rf ext/microchip/avr/ ext/microchip/sam/
	@$(MAKE) download-sources-microchip
