# Changelog

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
