# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Lookups

Answers about STM32 devices from the data in `ext/`: which CMSIS header a device
uses, which headers define a macro, register layouts from the header SVDs and
the Cortex-M core headers, and the sections, tables, registers and figures of
the documentation in the HTML archive (with a SQLite full text index in
`ext/cache/lookup`).

Every function takes and returns text. They are a CLI
(`python -m modm_data.lookup <tool> --help`) and an MCP server
(`python -m modm_data.lookup.mcp`, needs `modm-data[lookup]`).
"""
