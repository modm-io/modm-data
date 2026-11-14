# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# STMicro STM32Cube HAL2 Device Family Packs

The STM32 series supported by HAL2 (STM32C5, STM32G5, STM32H7P) are not part of
the STM32CubeMX database. Instead, their device family packs contain JSON
descriptors for the pinout, peripherals, DMA, interrupts, and clock tree of each
device, which are indexed by the CMSIS pack description file.
"""

from .device_data import (
    is_hal2_device,
    devices_from_prefix,
    devices_from_partname,
    temperature_codes,
)

__all__ = [
    "is_hal2_device",
    "devices_from_prefix",
    "devices_from_partname",
    "temperature_codes",
]
