# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# STMicro STM32CubeProgrammer Database

The STM32CubeProgrammer device database describes the memories of every device
ID (the DEV_ID of the DBGMCU, which CubeMX calls the die). It is the only
machine readable source of the internal flash organization: the banks and the
size of every page or sector, depending on the flash size and on the option
bits that select the dual-bank mode. It also has the address of the bootloader ID
in the system memory.
"""

from .flash import FlashLayout, flash_layouts
from .bootloader import system_bootloader

__all__ = ["FlashLayout", "flash_layouts", "system_bootloader"]
