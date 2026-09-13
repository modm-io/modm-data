# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @RaspberryPi Vendor Raspberry Pi \104

# =============================== Input Sources ===============================
ext/raspberrypi/svd/:
	@mkdir -p $@
	@curl -sfL https://github.com/raspberrypi/pico-sdk/raw/master/src/rp2040/hardware_regs/RP2040.svd -o $@/rp2040.svd
	@curl -sfL https://github.com/raspberrypi/pico-sdk/raw/master/src/rp2350/hardware_regs/RP2350.svd -o $@/rp2350.svd

.PHONY: download-sources-raspberrypi
## Download the RP2040 and RP2350 SVD files into /ext/raspberrypi.
download-sources-raspberrypi: ext/raspberrypi/svd/

.PHONY: update-sources-raspberrypi
## Update the RP2040 and RP2350 SVD files to the latest version.
update-sources-raspberrypi:
	@rm -rf ext/raspberrypi/svd/
	@$(MAKE) download-sources-raspberrypi
