# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

include tools/make/common.mk
include tools/make/arm.mk
include tools/make/stmicro.mk

# =============================== Input Sources ===============================
.PHONY: input-sources
## Download all input sources for all vendors.
## Warning: this downloads about ~10GB of data!
input-sources: clone-sources-stmicro download-stmicro-cubemx download-stmicro-pdfs
