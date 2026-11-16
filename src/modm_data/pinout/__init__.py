# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Device Pinout Pages

Generates the static pin mapping pages of data.modm.dev from the pinouts and
GPIO signals of the STM32, AVR, SAM, nRF and RP devices. The pages and the user
interface are the ones of the former `modm-pinout` project, whose scripts,
styles and templates are in modm-data-tools. Without templates only the pin
data of every source is written, which is attached to each release.
"""

from .devices import (
    atdf_devices,
    nrf_devices,
    payloads,
    pin_rows,
    rp_devices,
    stm32_devices,
    stm32_partnames,
)

__all__ = [
    "atdf_devices",
    "nrf_devices",
    "payloads",
    "pin_rows",
    "rp_devices",
    "stm32_devices",
    "stm32_partnames",
]
