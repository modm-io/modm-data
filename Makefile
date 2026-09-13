# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

include tools/make/common.mk
include tools/make/arm.mk
include tools/make/stmicro.mk
include tools/make/microchip.mk
include tools/make/nordic.mk
include tools/make/raspberrypi.mk

# =============================== Input Sources ===============================
.PHONY: input-sources
## Download all input sources for all vendors.
## Warning: this downloads about ~10GB of data!
input-sources: clone-sources-stmicro download-stmicro-cubemx download-stmicro-pdfs \
			   download-sources-microchip clone-sources-nordic download-sources-raspberrypi

.PHONY: device-sources
## Download all input sources for extracting the device data of all vendors.
## Note: The STMicro CubeMX database must be downloaded separately.
device-sources: clone-sources-stmicro download-sources-microchip clone-sources-nordic \
				download-sources-raspberrypi
