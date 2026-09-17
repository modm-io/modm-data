# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Peripheral Variants from SVD Files

Every STM32 device has its own register map, but the peripherals inside them
are reused across the entire device range. This module answers how many
*different* implementations of a peripheral actually exist by merging the
register maps of all peripheral instances of all devices into as few variants
as possible.

Each variant is one silicon implementation, described by a common core that
every instance implements and a set of optional features that only some
instances implement. Two variants of the same peripheral cannot be merged,
since they contradict each other, see `modm_data.svd2variants.merge` for what
exactly makes two register maps contradict.
"""

from .model import Field, Register, Shape, Instance, group_name
from .merge import MODES, ElementMap, Conflict, Rename, Difference, compare, scope_of
from .analyze import Feature, Member, Variant, Group, variants_of, conflicts_between
from .read import instances_of_svd, instances_of_svds

__all__ = [
    "Field",
    "Register",
    "Shape",
    "Instance",
    "group_name",
    "MODES",
    "ElementMap",
    "scope_of",
    "Conflict",
    "Rename",
    "Difference",
    "compare",
    "Feature",
    "Member",
    "Variant",
    "Group",
    "variants_of",
    "conflicts_between",
    "instances_of_svd",
    "instances_of_svds",
]
