# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Comparing Register Maps

Whether two register maps describe the same silicon cannot be decided by
comparing names alone, nor by comparing addresses alone:

- ST renames registers and bit fields without changing the layout, most
  visibly between the STM32H7x and STM32H7[RS]x devices. Comparing names alone
  reports these as different implementations, which they are not.
- ST reuses layouts for entirely different registers. The STM32F1 `GPIO_CRL`
  and the `GPIO_MODER` of every other device both start with two-bit fields at
  the same positions, so comparing addresses alone merges the two GPIO
  implementations into one, which they are not.

Therefore both keys are compared, but they are *not* symmetric:

- A name that appears at **two different locations** is a **conflict**: a
  driver cannot be written against both maps, so they are different versions.
  This is what separates the STM32F1 GPIO (`IDR` at `0x08`) from every other
  GPIO (`IDR` at `0x10`).
- A location that carries **two different names** is a **rename**: the silicon
  is the same and the names become aliases of each other.

Names alone miss two more kinds of contradiction, since unrelated elements
never share a name:

- Two bit fields with a different layout that **share bits** are a conflict,
  unless they are alternates of each other in one map, see `overlaps`. This is
  what separates the STM32L1 `COMP_CSR_OUTSEL[23:21]` from the STM32G0
  `COMP_CSR_BLANKING[24:20]`, which would otherwise each become an optional
  feature of the other.
- A rename is only believable **inside the same register**, see `reuses`. The
  STM32F373 `COMP_CSR_COMP1EN[0]` and the STM32H503 `COMP_SR_C1VAL[0]` share a
  bit, but the registers share neither a name nor a single bit field, so the
  bit was reused, not renamed.

Everything else is a *population* difference: an element that only exists in
one of the maps is an optional feature of the merged map.

## Binary and Source Compatibility

Many register maps differ only in *where* a register sits, while the register
itself is unchanged. Which of the two matters depends on how the hardware is
addressed, so the comparison runs in two modes, see `scope_of`:

- `binary`: elements are located by the address of their register, so moving a
  register to another offset is a conflict. This is what a driver that pokes
  at fixed addresses needs.
- `source`: elements are located by the *name* of their register, so a
  register may move as long as it keeps its name and its layout. This is what
  code written against a CMSIS header needs, since the structure member
  abstracts the offset away. A renamed register is a different register here,
  and simply becomes an optional feature of the merged map.
- `similar`: like `source`, but conflicts that only exist in the
  documentation are tolerated, see `documentation_only`.
"""

import re
from dataclasses import dataclass, field as _field
from collections import defaultdict
from .model import Shape, LocationKey, NameKey


@dataclass
class Conflict:
    """A name that is located differently in two register maps."""

    name: NameKey
    locations: set[LocationKey]
    other: set[LocationKey]

    @property
    def kind(self) -> str:
        """
        - `widened`: a bit field that starts at the same position but is longer
          in one of the maps, since ST tends to grow a field into the bits that
          were reserved before, for example the `IWDG_PR` prescaler.
        - `resized`: an element with the same name and address, but a different
          size, which is not merely an extension of the shorter one.
        - `moved`: an element with the same name at a different address.
        """
        locations = self.locations | self.other
        if self.name[0] == "F":
            # Same register and same start bit, only the length of the field differs
            return "widened" if len({location[1:3] for location in locations}) == 1 else "moved"
        return "resized" if len({location[1] for location in locations}) == 1 else "moved"

    def __str__(self) -> str:
        def location(keys):
            return "/".join(sorted(_format_location(key) for key in keys))

        return f"{_format_name(self.name)}: {self.kind} {location(self.locations)} != {location(self.other)}"


_INDEX = re.compile(r"_?\d+$")


@dataclass
class Overlap:
    """
    Two bit fields with a different layout that share bits, without being
    alternates of each other in any single register map.
    """

    location: LocationKey
    names: set[NameKey]
    other: LocationKey
    others: set[NameKey]

    @property
    def kind(self) -> str:
        return "overlap"

    def __str__(self) -> str:
        def label(location, names):
            return f"{'/'.join(sorted(name[-1] for name in names))}[{_format_range(location)}]"

        return (
            f"{_format_scope(self.location[1])}: {label(self.location, self.names)} "
            f"overlaps {label(self.other, self.others)}"
        )


def _format_range(location: LocationKey) -> str:
    position, width = location[2], location[3]
    return f"{position}" if width == 1 else f"{position + width - 1}:{position}"


def _same_field(names: set[NameKey], others: set[NameKey]) -> bool:
    """
    Whether two names describe the same bit field, which either the name rules
    already judge, or which one header describes bit by bit, e.g. `PLLM` and `PLLM_2`.
    """
    for name in names:
        for other in others:
            if name[-1] == other[-1] or _INDEX.sub("", name[-1]) == other[-1] or _INDEX.sub("", other[-1]) == name[-1]:
                return True
    return False


def overlaps(left: "ElementMap", right: "ElementMap") -> list[Overlap]:
    """
    Unrelated bit fields cannot share bits, but their names never collide, so
    the name rules cannot see it. The STM32L1 `COMP_CSR_OUTSEL[23:21]` and the
    STM32G0 `COMP_CSR_BLANKING[24:20]` are both at the same offset in the same
    register, and without this check they merge into one register map, where
    each becomes an optional feature of the other.

    :return: the new fields of `right` that overlap a differently laid out
             field of `left`, which `right` does not have as an alternate.
    """
    fields = defaultdict(list)
    for location in left.names:
        if location[0] == "F":
            fields[location[1]].append(location)
    found = []
    for location, names in right.names.items():
        if location[0] != "F" or location in left.names:
            continue
        _kind, scope, position, width = location
        for other in fields.get(scope, ()):
            if other[2] >= position + width or position >= other[2] + other[3]:
                continue
            # Alternates of the same register in one map may overlap, e.g. TIM_CCMR1 input and output
            if other in right.names or _same_field(names, left.names[other]):
                continue
            found.append(Overlap(location, set(names), other, set(left.names[other])))
    return found


@dataclass
class Reuse:
    """
    A location that is named differently in two register maps, but inside two
    unrelated registers, so the names only coincide in the bits they use.
    """

    location: LocationKey
    names: set[NameKey]
    others: set[NameKey]
    registers: set[str]
    other_registers: set[str]

    @property
    def kind(self) -> str:
        return "reused"

    def __str__(self) -> str:
        def label(names, registers):
            return f"{'/'.join(sorted(registers))}.{'/'.join(sorted(name[-1] for name in names))}"

        where = _format_scope(self.location[1])
        if self.location[0] == "F":
            where += f"[{_format_range(self.location)}]"
        return f"{where}: {label(self.names, self.registers)} reused as {label(self.others, self.other_registers)}"


def _register_names(elements: "ElementMap") -> dict:
    names = defaultdict(set)
    for location, keys in elements.names.items():
        if location[0] == "R":
            names[location[1]] |= {key[-1] for key in keys}
    return names


def reuses(left: "ElementMap", right: "ElementMap", renames: list["Rename"]) -> tuple[list[Reuse], list["Rename"]]:
    """
    A rename is only believable inside the same register. The STM32F373
    `COMP_CSR_COMP1EN[0]` and the STM32H503 `COMP_SR_C1VAL[0]` sit at the same
    bit, but the registers share neither a name nor a single bit field, so the
    bit was reused for something else rather than renamed. Registers count as
    the same if they share a name, or if at least one bit field has the same
    name at the same location, e.g. `GPIO_AFRL` and `GPIO_AFR[0]`.

    :return: the renames that are actually reuses, and the remaining renames.
    """
    left_registers, right_registers = _register_names(left), _register_names(right)
    shared = {
        location[1]
        for location, names in right.names.items()
        if location[0] == "F" and location in left.names and not left.names[location].isdisjoint(names)
    }
    # A register without bit fields on either side gives no evidence, e.g. an array whose bits were not named
    left_fields = {location[1] for location in left.names if location[0] == "F"}
    fields = left_fields & {location[1] for location in right.names if location[0] == "F"}

    reused, kept = [], []
    for rename in renames:
        scope = rename.location[1]
        same = not left_registers[scope].isdisjoint(right_registers[scope]) or scope in shared
        if same or scope not in fields:
            kept.append(rename)
        else:
            reused.append(
                Reuse(rename.location, rename.names, rename.other, left_registers[scope], right_registers[scope])
            )
    return reused, kept


@dataclass
class Rename:
    """A location that is named differently in two register maps."""

    location: LocationKey
    names: set[NameKey]
    other: set[NameKey]

    def __str__(self) -> str:
        def name(keys):
            return "/".join(sorted(_format_name(key) for key in keys))

        return f"{_format_location(self.location)}: {name(self.names)} = {name(self.other)}"


MODES = ("binary", "source", "similar")
"""The compatibility modes, see `scope_of` and `documentation_only`."""


def scope_of(register, mode: str):
    """
    The scope that an element is located in, which is what the two modes differ in:

    - `binary`: the offset of the register, so two maps are only compatible if
      every register is at the same address. This is what a driver that pokes
      at fixed addresses needs.
    - `source`: the name of the register, so the offsets are free to move as
      long as the register keeps its name and its layout. This is what code
      written against a CMSIS header needs, since the header abstracts the
      offsets away into structure members.
    - `similar`: like `source`, but differences that only exist in the
      documentation are no conflicts, see `documentation_only`.
    """
    return register.offset if mode == "binary" else register.name


def register_location(register, mode: str) -> LocationKey:
    return ("R", scope_of(register, mode), register.width, register.dim)


def register_name(register) -> NameKey:
    return ("R", register.name)


def field_location(register, field, mode: str) -> LocationKey:
    return ("F", scope_of(register, mode), field.position, field.width)


def field_name(register, field, mode: str) -> NameKey:
    return ("F", scope_of(register, mode), field.name)


def _format_scope(scope) -> str:
    return f"0x{scope:03x}" if isinstance(scope, int) else str(scope)


def _format_location(key: LocationKey) -> str:
    if key[0] == "R":
        return _format_scope(key[1])
    position, width = key[2], key[3]
    return f"{_format_scope(key[1])}[{position if width == 1 else f'{position + width - 1}:{position}'}]"


def _format_name(key: NameKey) -> str:
    return key[1] if key[0] == "R" else f"{_format_scope(key[1])}.{key[2]}"


class ElementMap:
    """
    A bidirectional map between the locations and the names of the elements of
    one or more register maps. Merged maps keep every alias of a location and
    are therefore able to detect a conflict with any of them.
    """

    def __init__(self):
        self.names: dict[LocationKey, set[NameKey]] = defaultdict(set)
        self.locations: dict[NameKey, set[LocationKey]] = defaultdict(set)

    @classmethod
    def from_shape(cls, shape: Shape, mode: str = "binary") -> "ElementMap":
        elements = cls()
        for register in shape.registers:
            elements.add(register_location(register, mode), register_name(register))
            for field in register.fields:
                elements.add(field_location(register, field, mode), field_name(register, field, mode))
        return elements

    def add(self, location: LocationKey, name: NameKey):
        self.names[location].add(name)
        self.locations[name].add(location)

    def update(self, other: "ElementMap"):
        for location, names in other.names.items():
            self.names[location] |= names
        for name, locations in other.locations.items():
            self.locations[name] |= locations

    @property
    def scopes(self) -> set:
        """The offsets or the names of all registers, depending on the mode."""
        return {location[1] for location in self.names if location[0] == "R"}

    def compare(self, other: "ElementMap") -> tuple[list[Conflict], list[Rename]]:
        """
        :return: the names that are located differently in `other` and the
                 locations that are named differently in `other`.
        """
        conflicts = [
            Conflict(name, set(self.locations[name]), set(locations))
            for name, locations in other.locations.items()
            if name in self.locations and self.locations[name].isdisjoint(locations)
        ]
        renames = [
            Rename(location, set(self.names[location]), set(names))
            for location, names in other.names.items()
            if location in self.names and self.names[location].isdisjoint(names)
        ]
        return conflicts, renames


@dataclass
class Difference:
    """The result of comparing two register maps."""

    conflicts: list[Conflict | Overlap | Reuse] = _field(default_factory=list)
    widened: list[Conflict] = _field(default_factory=list)
    tolerated: list = _field(default_factory=list)
    """Conflicts that only differ in the documentation, see `documentation_only`."""
    renames: list[Rename] = _field(default_factory=list)
    only_left: set[LocationKey] = _field(default_factory=set)
    only_right: set[LocationKey] = _field(default_factory=set)
    disjoint: bool = False

    @property
    def compatible(self) -> bool:
        """Whether both register maps can be merged into one."""
        return not self.conflicts and not self.disjoint

    @property
    def identical(self) -> bool:
        """Whether both register maps describe the same elements at the same locations."""
        return self.compatible and not self.only_left and not self.only_right and not self.renames

    @property
    def relation(self) -> str:
        if not self.compatible:
            return "incompatible"
        if not self.only_left and not self.only_right and not self.widened:
            return "renamed" if self.renames else "identical"
        if not self.only_right:
            return "superset"
        if not self.only_left:
            return "subset"
        return "overlapping"


def _bits(location: LocationKey) -> set[int]:
    return set(range(location[2], location[2] + location[3]))


def _plain(name: str) -> str:
    """The name without indices, placeholders and separators, e.g. `COMPBLANKING` for `COMPxBLANKING`."""
    return re.sub(r"[\d_]|x(?=[A-Z])", "", name).upper()


def _related(names: set[NameKey], others: set[NameKey], width: int) -> bool:
    """
    Whether two bit field names are the same without their indices, e.g. `EM_12`
    and `EM47`, one contains the other, e.g. `BLANKING` and `COMPxBLANKING`, or
    they start the same, e.g. `FPU_IE` and `FPU_IOIE`.

    Different indices usually mean different instances of a bit field, e.g. the
    `LPGPIO_MODER_MOD1[1]` of pin 1 inside the `GPIO_MODER_MODE0[1:0]` of pin 0,
    unless the wider bit field is a mask of many, e.g. `EXTI_EMR2_EM[31:15]`.

    :param width: the width of the wider bit field.
    """
    for name in names:
        for other in others:
            indices = [re.findall(r"\d+", key[-1]) for key in (name, other)]
            if all(indices) and indices[0] != indices[1] and width < 4:
                continue
            short, long = sorted((_plain(name[-1]), _plain(other[-1])), key=len)
            if short and (short == long or (len(short) >= 4 and short in long) or long.startswith(short[:3])):
                return True
    return False


def documentation_only(conflicts: list) -> tuple[list, list]:
    """
    Separates the conflicts that are only differences in how a register is
    documented from the ones that are differences in the silicon:

    - A register declared with another width, e.g. `USART_RDR` as `uint16_t`
      in the STM32F0 header and as `uint32_t` everywhere else.
    - A bit field with the same name over overlapping bits, e.g. a mask of many
      bit fields that covers a different range.
    - A bit field described as smaller bit fields with a related name, e.g.
      `COMP_CSR_BLANKING` and `COMP_CSR_COMPxBLANKING`.
    - A bit field of at least four bits described as smaller bit fields that
      tile one of its edges, if they are a value split into at least two parts
      of at least four bits, e.g. `USART_BRR_BRR` as `USART_BRR_DIV_MANTISSA`
      and `USART_BRR_DIV_FRACTION`, or flags at the edge of a value that take
      up at most a quarter of it, e.g. the `TIM_CNT_UIFCPY` flag in the top bit
      of `TIM_CNT_CNT`.

    Unrelated bit fields nest by coincidence surprisingly often, which is not
    tolerated: small ones, e.g. `DBGMCU_CR_TRACE_MODE[7:6]` and
    `DBGMCU_CR_DBG_STOPD3[7]`, flags in another position, e.g. the STM32F3
    `SYSCFG_CFGR1_FPU_IE[31:26]` and the STM32F0 `SYSCFG_CFGR1_USART3_DMA_RMP[26]`,
    and single wide bit fields, e.g. the STM32F4 `FLASH_OPTCR1_nWRP[27:16]` and
    the STM32F7 `FLASH_OPTCR1_BOOT_ADD1[31:16]`.

    :return: the conflicts that differ in the silicon and the ones that don't.
    """
    # All bit fields that nest inside a wider bit field, grouped by the wider one
    nested, pairs = defaultdict(list), {}
    for conflict in conflicts:
        if isinstance(conflict, Overlap):
            this, that = (conflict.location, conflict.names), (conflict.other, conflict.others)
            inner, outer = sorted((this, that), key=lambda field: field[0][3])
            if _bits(inner[0]) <= _bits(outer[0]):
                nested[outer[0]].append(inner[0])
                pairs[id(conflict)] = (inner, outer)

    def decomposes(outer: LocationKey) -> bool:
        inners = nested[outer]
        bits = set().union(*(_bits(inner) for inner in inners))
        low, high = min(bits), max(bits)
        edge = bits == set(range(low, high + 1)) and (low == outer[2] or high == outer[2] + outer[3] - 1)
        if outer[3] < 4 or not edge:
            return False
        parts = len(inners) >= 2 and all(inner[3] >= 4 for inner in inners)
        flags = len(bits) * 4 <= outer[3]
        return parts or flags

    silicon, documentation = [], []
    for conflict in conflicts:
        if isinstance(conflict, Conflict):
            locations = conflict.locations | conflict.other
            same_bits = conflict.name[0] == "F" and bool(set.intersection(*(_bits(ll) for ll in locations)))
            (documentation if conflict.kind == "resized" or same_bits else silicon).append(conflict)
        elif (pair := pairs.get(id(conflict))) is not None:
            inner, outer = pair
            related = _related(inner[1], outer[1], outer[0][3])
            (documentation if related or decomposes(outer[0]) else silicon).append(conflict)
        else:
            silicon.append(conflict)
    return silicon, documentation


def compare(left: ElementMap, right: ElementMap, widening: bool = True, similar: bool = False) -> Difference:
    """
    Compares two register maps, see the module documentation for the rules.

    :param left: the register map to compare against, may be a merged map.
    :param right: the register map to compare.
    :param widening: whether a bit field that only grew into the reserved bits
                     above it is an optional extension instead of a conflict.
    :param similar: whether differences in the documentation are tolerated, see
                    `documentation_only`.
    :return: the differences between both maps.
    """
    conflicts, renames = left.compare(right)
    conflicts += overlaps(left, right)
    reused, renames = reuses(left, right, renames)
    conflicts += reused
    widened = []
    if widening:
        widened = [conflict for conflict in conflicts if conflict.kind == "widened"]
        conflicts = [conflict for conflict in conflicts if conflict.kind != "widened"]
    tolerated = []
    if similar:
        conflicts, tolerated = documentation_only(conflicts)
    # Two maps without a single common register describe unrelated hardware
    disjoint = bool(left.names) and bool(right.names) and left.scopes.isdisjoint(right.scopes)
    return Difference(
        conflicts=conflicts,
        widened=widened,
        tolerated=tolerated,
        renames=renames,
        only_left=set(left.names) - set(right.names),
        only_right=set(right.names) - set(left.names),
        disjoint=disjoint,
    )
