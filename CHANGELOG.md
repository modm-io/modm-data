# Changelog

## 0.0.4

- Move the data extraction pipelines of all vendors from modm-devices.
- Add Microchip AVR and SAM device pack downloader and ATDF parser.
- Add Nordic nRF5x device data from the nrfx MDK and stored product specification pinouts.
- Add Raspberry Pi RP2040 and RP2350 device data from the Pico SDK SVD files.
- Fix STM32 Flash sizes of STM32L471ZGJx, STM32L476ZGTxP, STM32L496AEIx, STM32F730x8, and STM32F750x8.
- Fix missing STM32 alternate function numbers for remapped pins and differently named signals.
- Fix STM32 signal names for Ethernet CRS_DV, WL debug signals, and signals without peripheral prefix.
- Assign STM32 DMA requests to the peripheral instance and ignore more CubeMX software modules.
- Reconstruct the STM32 SVD files from the compiled CMSIS headers and the CubeHAL sources.
- Publish the STM32 SVD files with the SVD Explorer on the homepage and attach them to releases.

## 0.0.3

- Import STM32 Open-CMSIS-Pack DFP data sources.
- Use DFP sources for memory map and CPU frequency.
- Import latest changes from modm-devices for new STM32 devices.
- Update dependencies and frozen requirements.txt.


## 0.0.2

- Update the regression tests and enforce it via CI.
- Factor out common pdf2html code from the STM32 specialization.
- Enforce code style using Ruff in the CI.
- Update dependencies and frozen requirements.txt.
- Add downloader code for CubeProg database.
- Import latest changes from modm-devices for new STM32 devices.
- Replace OWLready with Kuzu KG implementation for storing all data.
- Publish KG database as artifact to GitHub releases.


## 0.0.1

Import from [the JSys paper artifact](https://github.com/salkinium/pdf-data-extraction-jsys-artifact)
with the following changes:

- Significant structural refactoring to separate submodules more cleanly.
- Import of the modm-devices code for reading the CubeMX database.
- Scoping of STM32 device ontology into function.
- Removal of PNG support for rendering PDF in debug mode.
- Refactoring to support pypdfium ≥4.18.0 and use more of it natively.
- Add proper packaging support with pyproject.toml.
- Move scripts into submodules `__main__.py` files for clean CLI.
- Use Python ≥3.11.
- Remove broken ASCII debug rendering for PDF pages.
- Split and cleanup Makefiles to be more useful.
- Add full API documentation for `modm_data.pdf` submodule.
- Simplify copyright header.
