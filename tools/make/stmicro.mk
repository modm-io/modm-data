# Copyright 2023, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

### @STM Vendor STMicroelectronics \101

# =============================== Input Sources ===============================
ext/stmicro/cubehal/:
	@git clone --depth=1 git@github.com:modm-ext/stm32-cube-hal-drivers.git $@

ext/stmicro/header/:
	@git clone --depth=1 git@github.com:modm-io/cmsis-header-stm32.git $@

ext/stmicro/svd/:
	@git clone --depth=1 git@github.com:modm-io/cmsis-svd-stm32.git $@

ext/stmicro/owl-archive/:
	@git clone --depth=1 git@github.com:modm-ext/archive-stmicro-owl.git $@

ext/stmicro/svd-archive/:
	@git clone --depth=1 git@github.com:modm-ext/archive-stmicro-svd.git $@

.PHONY: clone-sources-stmicro
## Clone all STMicro related repositories into /ext/stmicro.
clone-sources-stmicro: clone-sources-arm ext/stmicro/cubehal/ ext/stmicro/header/ \
					   ext/stmicro/svd/ ext/stmicro/owl-archive/ ext/stmicro/svd-archive/

.PHONY: update-sources-stmicro
## Update all STMicro related repositories to the latest version.
update-sources-stmicro: update-sources-arm
	@(cd ext/stmicro/cubehal && git fetch && git reset --hard origin/main) &
	@(cd ext/stmicro/header && git fetch && git reset --hard origin/master) &
	@(cd ext/stmicro/svd && git fetch && git reset --hard origin/main) &
	@(cd ext/stmicro/owl-archive && git fetch && git reset --hard origin/main) &
	@(cd ext/stmicro/svd-archive && git fetch && git reset --hard origin/master) &
	@wait


.PHONY: download-stmicro-pdfs
## Download all PDFs from the vendor's website.
## Warning: this downloads about ~10GB of data!
download-stmicro-pdfs:
	@python3 -m modm_data.dl.stmicro --directory ext/stmicro/pdf --download pdf

.PHONY: download-stmicro-cubemx
## Download the CubeMX database from ST's update server.
download-stmicro-cubemx:
	@python3 -m modm_data.dl.stmicro --directory ext/stmicro/cubemx --download cubemx --patch


# ========================== Private Input Sources  ===========================
# Please ping @salkinium for access.

# Note: The STMicro CubeMX database archive repo is private and used for our CI.
#       Please download the database via 'make download-stmicro-cubemx'!
ext/stmicro/cubemx/:
	@git clone --depth=1 git@github.com:modm-ext/archive-stmicro-cubemx.git $@

# Note: The STMicro HTML archive repo is private and used for our CI.
#       Please convert the HTMLs via 'make convert-stmicro-html'.
ext/stmicro/html-archive/:
	@git clone --depth=1 git@github.com:modm-ext/archive-stmicro-html.git $@

# Note: The STMicro PDF archive repo is private and used for our CI.
#       Please download the PDFs via 'make download-stmicro-pdfs'.
ext/stmicro/pdf/:
	@git clone --depth=1 git@github.com:modm-ext/archive-stmicro-pdf.git $@

.PHONY: clone-sources-stmicro-private
clone-sources-stmicro-private: clone-sources-stmicro ext/stmicro/cubemx/ \
							   ext/stmicro/pdf/ ext/stmicro/html-archive/

.PHONY: update-sources-stmicro-private
update-sources-stmicro-private: update-sources-stmicro
	@(cd ext/stmicro/cubemx && git fetch && git reset --hard origin/main) &
	@(cd ext/stmicro/html-archive && git fetch && git reset --hard origin/main) &
	@(cd ext/stmicro/pdf && git fetch && git reset --hard origin/main) &
	@wait


# ========================== Converting PDF to HTML ===========================
ext/stmicro/html-archive/%: ext/stmicro/pdf/%.pdf log/stmicro/html/
	@echo "Converting" $< "->" $@ "+" $(@:ext/stmicro/html-archive/%=log/stmicro/html/%.txt)
	@-python3 -m modm_data.pdf2html.stmicro --document $< --output $@ --html --parallel

.PHONY: convert-stmicro-html-%
## Convert a document from the PDF archive into a folder in the HTML
## archive. The log will be placed in log/stmicro/html/%.txt.
convert-stmicro-html-%: ext/stmicro/html-archive/%

stmicro_pdf2html = $(sort $(1:ext/stmicro/pdf/%.pdf=ext/stmicro/html-archive/%))
.PHONY: convert-stmicro-html-rm
## Convert all STMicro Reference Manual PDFs into HTML.
convert-stmicro-html-rm: $(stmicro_pdf2html $(wildcard ext/stmicro/pdf/RM*.pdf))

.PHONY: convert-stmicro-html-ds
## Convert all STMicro Datasheet PDFs into HTML.
convert-stmicro-html-ds: $(stmicro_pdf2html $(wildcard ext/stmicro/pdf/DS*.pdf))

.PHONY: convert-stmicro-html
## Convert all STMicro PDFs into HTML.
convert-stmicro-html: $(stmicro_pdf2html $(wildcard ext/stmicro/pdf/*.pdf))

.PHONY: clean-stmicro-html-%
## Remove all STMicro HTML folders of a specific document number.
clean-stmicro-html-%:
	@rm -rf $(wildcard $(@:clean-stmicro-html-%=ext/stmicro/html-archive/%*-v*))

.PHONY: clean-stmicro-html
## Remove all STMicro HTML folders in the archive.
clean-stmicro-html:
	@rm -rf $(wildcard ext/stmicro/html-archive/*-v*)




# ========================== Converting CubeMX to OWL =========================
.PHONY: convert-stmicro-cube-owl-%
convert-stmicro-cube-owl-%: log/stmicro/owl/ ext/stmicro/cubemx
	@echo "Converting CubeMX database to OWL Graphs."
	@-python3 -m modm_data.cube2owl --family $(@:convert-stmicro-cube-owl-%=stm32%)

.PHONY: convert-stmicro-cube-owl
convert-stmicro-cube-owl: convert-stmicro-cube-owl-f4

.PHONY: clean-stmicro-cube-owl
clean-stmicro-cube-owl:
	@rm -f $(wildcard ext/stmicro/owl-archive/cube_*.owl)


# ========================== Converting HTML to OWL ===========================
ext/stmicro/owl-archive/html_%.owl: ext/stmicro/html-archive/% log/stmicro/owl/
	@echo "Converting" $< "->" $@ "+" $(@:ext/stmicro/owl-archive/%.owl=log/stmicro/owl/html_%.txt)
	@-python3 -m modm_data.html2owl.stmicro --document $< > \
			$(@:ext/stmicro/owl-archive/%.owl=log/stmicro/owl/html_%.txt) 2>&1

.PHONY: convert-stmicro-html-owl
convert-stmicro-html-owl: ext/stmicro/owl-archive/ log/stmicro/owl/
	@echo "Converting all HTML Files to OWL Graphs."
	@-python3 -m modm_data.html2owl.stmicro --all

.PHONY: clean-stmicro-html-owl
clean-stmicro-html-owl:
	@rm -f $(wildcard ext/stmicro/owl-archive/html_*.owl)


# ========================== Converting HTML to SVD ===========================
.PHONY: convert-stmicro-html-svd-%
convert-stmicro-html-svd-%: log/stmicro/svd/ ext/stmicro/svd-archive/ ext/arm/cmsis
	@python3 -m modm_data.html2svd.stmicro --document $(@:convert-stmicro-html-svd-%=%)

.PHONY: convert-stmicro-html-svd
convert-stmicro-html-svd: ext/stmicro/html-archive/
	@echo "Converting all HTML Files to SVD."
	@-python3 -m modm_data.html2svd.stmicro --all

.PHONY: clean-stmicro-html-svd
clean-stmicro-html-svd:
	@rm -f $(wildcard ext/stmicro/svd-archive/html_*.svd)


# ========================= Converting Header to SVD ==========================
.PHONY: convert-stmicro-header-svd-%
## Convert a C header file from the CMSIS header archive into a memory map in
## the SVD archive. The log will be placed in log/stmicro/svd/header_%.txt.
convert-stmicro-header-svd-%: log/stmicro/svd/ ext/stmicro/header/ ext/arm/cmsis/
	@-python3 -m modm_data.header2svd.stmicro $(@:convert-stmicro-header-svd-%=%) > \
			$(@:convert-stmicro-header-svd-%=log/stmicro/svd/header_%.txt) 2>&1

# We are ignoring L5 U5 WB WL due to ARMv8-M S/NS aliasing and issues in headers
.PHONY: convert-stmicro-header-svd
## Convert all STMicro CMSIS header files into SVD files.
convert-stmicro-header-svd: log/stmicro/svd/ ext/stmicro/header/ ext/arm/cmsis/
	@echo "Converting all CMSIS Headers to SVD."
	@-python3 -m modm_data.header2svd.stmicro \
		--all stm32f0 --all stm32f1 --all stm32f2 \
		--all stm32f3 --all stm32f4 --all stm32f7 \
		--all stm32g0 --all stm32g4 --all stm32h7 \
		--all stm32l0 --all stm32l1 --all stm32l4

.PHONY: clean-stmicro-header-svd
## Remove all STMicro SVD files in the archive.
clean-stmicro-header-svd:
	@rm -f $(wildcard ext/stmicro/svd-archive/header_*.svd)
