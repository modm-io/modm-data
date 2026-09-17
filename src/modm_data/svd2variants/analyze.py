# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Condensing Register Maps into Variants

All instances of a peripheral group are first deduplicated into distinct
*shapes*, then greedily merged into *variants*: a variant is a set of shapes
that can be merged without a conflict, and therefore describes one silicon
implementation of the peripheral.

The merged register map of a variant is split into a **core**, which every
instance of the variant implements, and a set of **features**, which are the
elements that only some instances implement. A feature is not a single
register or bit field, but every element that appears in exactly the same
instances, since those are enabled and disabled together, for example the
second half of the CAN filter banks or the clock enable bits of a peripheral
that is only bonded out on the larger packages.
"""

import re
from dataclasses import dataclass, field as _field
from collections import defaultdict
from .model import Shape, Instance, LocationKey, IGNORED_REGISTERS
from .merge import ElementMap, Conflict, Rename, compare, register_location, field_location
from .naming import name_feature


@dataclass
class Feature:
    """The elements of a variant that are implemented by the same instances."""

    locations: set[LocationKey]
    shapes: frozenset[int] = _field(repr=False)
    instances: int = 0
    name: str = ""
    elements: list = _field(default_factory=list, repr=False)

    @property
    def registers(self) -> set[int]:
        return {location[1] for location in self.locations if location[0] == "R"}


@dataclass
class Member:
    """One distinct register map of a variant and every instance that has it."""

    shape: Shape = _field(repr=False)
    elements: ElementMap = _field(repr=False)
    instances: list[Instance] = _field(default_factory=list, repr=False)

    @property
    def devices(self) -> set[str]:
        return {instance.device for instance in self.instances}


@dataclass
class Variant:
    """One silicon implementation of a peripheral group."""

    group: str
    members: list["Member"] = _field(default_factory=list, repr=False)
    elements: ElementMap = _field(default_factory=ElementMap, repr=False)
    renames: list[Rename] = _field(default_factory=list, repr=False)
    relocated: list[Conflict] = _field(default_factory=list, repr=False)
    core: set[LocationKey] = _field(default_factory=set, repr=False)
    features: list[Feature] = _field(default_factory=list, repr=False)

    @property
    def shapes(self) -> list[Shape]:
        return [member.shape for member in self.members]

    @property
    def instances(self) -> list[Instance]:
        return [instance for member in self.members for instance in member.instances]

    @property
    def devices(self) -> set[str]:
        return {instance.device for member in self.members for instance in member.instances}

    @property
    def names(self) -> set[str]:
        return {instance.name for member in self.members for instance in member.instances}

    def add(self, member: "Member", renames: list[Rename], relocated: list[Conflict]):
        self.members.append(member)
        self.elements.update(member.elements)
        self.renames += renames
        self.relocated += relocated

    def described(self, mode: str) -> dict[LocationKey, tuple[str, str, str, str]]:
        """:return: the kind, register name, name and description of every element."""
        described = {}
        for member in self.members:
            for register in member.shape.registers:
                described.setdefault(
                    register_location(register, mode), ("R", register.name, register.name, register.description)
                )
                for field in register.fields:
                    described.setdefault(
                        field_location(register, field, mode), ("F", register.name, field.name, field.description)
                    )
        return described

    def condense(self, mode: str = "binary", names: dict[str, str] = None):
        """Splits the merged register map into the common core and the optional features."""
        presence = defaultdict(set)
        instances = defaultdict(int)
        for index, member in enumerate(self.members):
            for location in member.elements.names:
                presence[location].add(index)
                instances[location] += len(member.instances)
        total = len(self.members)
        self.core = {location for location, shapes in presence.items() if len(shapes) == total}
        features = defaultdict(set)
        for location, shapes in presence.items():
            if len(shapes) != total:
                features[frozenset(shapes)].add(location)
        self.features = sorted(
            (
                Feature(locations, shapes, max(instances[location] for location in locations))
                for shapes, locations in features.items()
            ),
            key=lambda f: (-f.instances, -len(f.locations)),
        )
        described = self.described(mode)
        for feature in self.features:
            feature.elements = [described[ll] for ll in sorted(feature.locations) if ll in described]
            feature.name = name_feature(feature.elements, names)


@dataclass
class Group:
    """All instances of one peripheral type across all devices."""

    name: str
    instances: list[Instance] = _field(default_factory=list, repr=False)
    variants: list[Variant] = _field(default_factory=list, repr=False)
    mode: str = "binary"
    ignored: list[str] = _field(default_factory=list)
    """The names of the registers that were left out of the comparison."""

    @property
    def shapes(self) -> int:
        return sum(len(variant.shapes) for variant in self.variants)

    @property
    def devices(self) -> set[str]:
        return {instance.device for instance in self.instances}


def _shapes(instances: list[Instance], ignored: re.Pattern = None) -> dict[Shape, list[Instance]]:
    """Deduplicates the instances by their register map without the ignored registers."""
    shapes = defaultdict(list)
    for instance in instances:
        shapes[instance.shape.without(ignored) if ignored else instance.shape].append(instance)
    # The largest register maps discriminate best and therefore seed the variants
    return dict(sorted(shapes.items(), key=lambda s: (-len(s[0]), -len(s[1]))))


def variants_of(
    name: str, instances: list[Instance], widening: bool = True, mode: str = "binary", names: dict[str, str] = None
) -> Group:
    """
    Merges all instances of a peripheral group into as few variants as possible.

    Every shape is merged into the variant it shares the most elements with, so
    that a shape that is compatible with several variants does not merely join
    the first one. A shape that conflicts with every variant starts a new one.

    :param name: the name of the peripheral group, for example `TIM`.
    :param instances: all instances of the group across all devices.
    :param widening: whether a bit field that only grew into the reserved bits
                     above it is an optional extension instead of a conflict.
    :param mode: `binary` locates elements by register address, `source` by
                 register name, and `similar` also tolerates differences in the
                 documentation, see `modm_data.svd2variants.merge.scope_of`.
    :param names: the lookup table of generated feature names, see
                  `modm_data.svd2variants.annotate`.
    :return: the group with its variants.
    """
    group = Group(name, instances, mode=mode)
    if ignored := IGNORED_REGISTERS.get(name):
        group.ignored = sorted({r.name for i in instances for r in i.shape.registers if ignored.match(r.name)})
    for shape, shape_instances in _shapes(instances, ignored).items():
        member = Member(shape, ElementMap.from_shape(shape, mode), shape_instances)
        best, shared, renames, relocated = None, -1, [], []
        for variant in group.variants:
            difference = compare(variant.elements, member.elements, widening, mode)
            if not difference.compatible:
                continue
            common = len(set(variant.elements.names) & set(member.elements.names))
            if common > shared:
                best, shared = variant, common
                renames, relocated = difference.renames, difference.relocated
        if best is None:
            best = Variant(name)
            group.variants.append(best)
            renames, relocated = [], []
        best.add(member, renames, relocated)

    # A merged map collects aliases that are later evidence for a rename, so a
    # conflict that kept two shapes apart may be gone once both variants are
    # complete. Keep merging the smallest variant into its best match until no
    # two variants are compatible anymore.
    merged = True
    while merged:
        merged = False
        for small in sorted(group.variants, key=lambda v: len(v.instances)):
            best, shared, renames, relocated = None, -1, [], []
            for large in group.variants:
                if large is small:
                    continue
                difference = compare(large.elements, small.elements, widening, mode)
                if not difference.compatible:
                    continue
                common = len(set(large.elements.names) & set(small.elements.names))
                if common > shared:
                    best, shared = large, common
                    renames, relocated = difference.renames, difference.relocated
            if best is not None:
                for member in small.members:
                    best.add(member, [], [])
                best.renames += small.renames + renames
                best.relocated += small.relocated + relocated
                group.variants.remove(small)
                merged = True
                break

    for variant in group.variants:
        variant.condense(mode, names)
    group.variants.sort(key=lambda v: (-len(v.instances), -len(v.shapes)))
    return group


def conflicts_between(variants: list[Variant], mode: str = "binary") -> dict[tuple[int, int], list[Conflict]]:
    """:return: the conflicts between each pair of variants, which is why they are separate."""
    conflicts = {}
    for left in range(len(variants)):
        for right in range(left + 1, len(variants)):
            difference = compare(variants[left].elements, variants[right].elements, mode=mode)
            conflicts[(left, right)] = difference.conflicts
    return conflicts
