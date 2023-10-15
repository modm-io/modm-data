# Copyright (c) 2023, Niklas Hauser
#
# This file is part of the modm-data project.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# -----------------------------------------------------------------------------

include tools/make/common.mk
include tools/make/arm.mk
include tools/make/stmicro.mk

# =============================== Input Sources ===============================
.PHONY: input-sources
input-sources: clone-sources-stmicro download-stmicro-cubemx download-stmicro-pdfs
