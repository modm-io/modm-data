# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Microchip ATDF Device Descriptions

Parses the ATDF files contained in the Microchip device packs for AVR and SAM
devices.
"""

from . import avr, sam
from .identifier import avr_did_from_string, sam_did_from_string

__all__ = [
    "avr",
    "sam",
    "avr_did_from_string",
    "sam_did_from_string",
]
