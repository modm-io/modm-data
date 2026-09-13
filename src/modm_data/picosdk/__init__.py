# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Raspberry Pi Pico SDK

Parses the CMSIS-SVD files of the RP2040 and RP2350 devices from the Pico SDK.
"""

from .device_data import device_files, device_from_file, did_from_string

__all__ = [
    "device_files",
    "device_from_file",
    "did_from_string",
]
