# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from .reference import memory_map_from_reference_manual
from .datasheet import memory_map_from_datasheet

__all__ = [
    "memory_map_from_reference_manual",
    "memory_map_from_datasheet",
]
