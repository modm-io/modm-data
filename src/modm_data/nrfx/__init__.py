# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Nordic Semiconductor nrfx MDK

Parses the linker scripts and CMSIS-SVD files of the nRF51, nRF52, and nRF53
devices from the nrfx MDK. The package pinouts are extracted from the pin
assignment chapters of the product specifications and stored as JSON, since
the Nordic documentation cannot be downloaded automatically. See
`modm_data.nrfx.pinout` for how to update the pinout data.
"""

from .device_data import device_files, device_from_file, did_from_string
from .pinout import pinout, pinout_from_html, write_pinout_json

__all__ = [
    "device_files",
    "device_from_file",
    "did_from_string",
    "pinout",
    "pinout_from_html",
    "write_pinout_json",
]
