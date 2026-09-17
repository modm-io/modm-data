# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
The canonical description of a peripheral register map and its atomic elements.

A register map is compared as a set of *elements*, where every element has both
a *location* and a *name*:

- a register: `("R", offset, width, dim)` located, `("R", name)` named.
- a bit field inside that register: `("F", offset, position, width)` located,
  `("F", offset, name)` named, so that the field names of a renamed register
  still line up.

Comparing both keys separately is what makes the difference between a register
map that was merely renamed and one that was actually changed, see
`modm_data.svd2variants.merge`.
"""

import re
from dataclasses import dataclass, field as _field

# Locations and names are compared as plain tuples for speed
LocationKey = tuple
NameKey = tuple


@dataclass(frozen=True, slots=True, order=True)
class Field:
    """A bit field of a register."""

    position: int
    width: int
    name: str
    # Descriptions are documentation, not layout, and must not split a variant
    description: str = _field(default="", compare=False)

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.position

    @property
    def bit_range(self) -> str:
        return f"{self.position}" if self.width == 1 else f"{self.position + self.width - 1}:{self.position}"


@dataclass(frozen=True, slots=True, order=True)
class Register:
    """A register with its bit fields."""

    offset: int
    name: str
    width: int = 4
    dim: int = 0
    fields: tuple[Field, ...] = ()
    description: str = _field(default="", compare=False)
    # The CMSIS headers declare almost every register `__IO`, so the access is too
    # sparse to tell implementations apart and is only carried along for display
    access: str = _field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class Shape:
    """
    The register map of one peripheral instance, normalized so that two
    instances with the same memory layout compare and hash equal.

    Registers sharing an offset are alternates of each other (unions in the
    CMSIS header, for example `TIM_CCMR1` in output and input mode). They are
    kept as separate registers, but their fields share one location scope,
    since they describe the same physical register.
    """

    registers: tuple[Register, ...]

    @staticmethod
    def from_peripheral(peripheral) -> "Shape":
        """:param peripheral: a `modm_data.svd.Peripheral` tree node."""
        registers = []
        for register in peripheral.children:
            fields = tuple(
                sorted(
                    Field(f.position, f.width, f.name, getattr(f, "description", "") or "") for f in register.children
                )
            )
            registers.append(
                Register(
                    register.offset,
                    register.name,
                    register.width,
                    getattr(register, "dim", 0) or 0,
                    fields,
                    getattr(register, "description", "") or "",
                    getattr(register, "access", "") or "",
                )
            )
        return Shape(tuple(sorted(registers)))

    def without(self, pattern: re.Pattern) -> "Shape":
        """:return: the register map without the registers whose name matches the pattern."""
        return Shape(tuple(register for register in self.registers if not pattern.match(register.name)))

    @property
    def offsets(self) -> set[int]:
        return {register.offset for register in self.registers}

    def __len__(self) -> int:
        return len(self.registers) + sum(len(register.fields) for register in self.registers)

    def __hash__(self) -> int:
        return hash(self.registers)


IGNORED_REGISTERS: dict[str, re.Pattern] = {
    # The option registers route device signals to the timer inputs, e.g. which
    # comparator drives the break input or which pin is remapped to TI1. Their
    # layout follows the device, not the timer, so they would split every timer
    # implementation into one variant per device family.
    "TIM": re.compile(r"^(OR\d?|AF\d|TISEL)$"),
}
"""Registers per peripheral group that are not part of the silicon implementation."""


@dataclass
class Instance:
    """One peripheral of one device."""

    device: str
    name: str
    group: str
    address: int
    shape: Shape = _field(repr=False)


def group_name(peripheral) -> str:
    """
    :param peripheral: a `modm_data.svd.Peripheral` tree node.
    :return: the CMSIS structure type of the peripheral, for example `TIM` for
             `TIM_TypeDef`, or the instance name without its index as fallback.
    """
    if group := getattr(peripheral, "group", None):
        return group
    return re.sub(r"\d+", "", peripheral.name) or peripheral.name
