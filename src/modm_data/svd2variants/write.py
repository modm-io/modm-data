# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Serializes the peripheral variants into the JSON files that the Peripheral
Variant Explorer loads: one index of all groups and one file per group.

Device names are shared per file and referenced by index, since the same few
hundred names would otherwise be repeated for every single variant, feature
and register map.
"""

import json
from pathlib import Path
from collections import defaultdict
from .analyze import Group, Variant, conflicts_between
from .merge import ElementMap, register_location, field_location


def _names(elements: ElementMap, location: tuple) -> list[str]:
    """:return: every name the element is known under, the most common one first."""
    names = elements.names.get(location, set())
    return sorted(name[-1] for name in names)


def _register_map(variant: Variant, features: dict[tuple, int], mode: str) -> list[dict]:
    """The merged register map of the variant, with every element tagged by its feature."""
    registers = {}
    descriptions = {}
    offsets = defaultdict(set)
    accesses = defaultdict(set)
    for member in variant.members:
        for register in member.shape.registers:
            location = register_location(register, mode)
            entry = registers.setdefault(
                location,
                {
                    "width": register.width,
                    "dim": register.dim,
                    "names": _names(variant.elements, location),
                    "feature": features.get(location, -1),
                    "fields": {},
                },
            )
            # In source mode the same register may sit at a different offset per instance
            offsets[location].add(register.offset)
            descriptions.setdefault(location, register.description)
            if register.access:
                accesses[location].add(register.access)
            for field in register.fields:
                flocation = field_location(register, field, mode)
                entry["fields"].setdefault(
                    flocation,
                    {
                        "position": field.position,
                        "width": field.width,
                        "names": _names(variant.elements, flocation),
                        "feature": features.get(flocation, -1),
                        "description": field.description,
                    },
                )
    for location, entry in registers.items():
        entry["description"] = descriptions.get(location, "")
        entry["offset"] = min(offsets[location])
        # Instances may disagree, e.g. a register that only some headers declare read-only
        entry["access"] = sorted(accesses[location])
        if len(offsets[location]) > 1:
            entry["offsets"] = sorted(offsets[location])
        entry["fields"] = sorted(entry.pop("fields").values(), key=lambda f: f["position"])
    return sorted(registers.values(), key=lambda r: (r["offset"], r["names"]))


def _variant(variant: Variant, index: int, devices: dict[str, int], conflicts: dict, mode: str) -> dict:
    features = {location: index for index, feature in enumerate(variant.features) for location in feature.locations}
    return {
        "instances": len(variant.instances),
        "devices": sorted(devices[device] for device in variant.devices),
        "names": sorted(variant.names),
        "members": [
            {
                "instances": len(member.instances),
                "devices": sorted(devices[device] for device in member.devices),
                "names": sorted({instance.name for instance in member.instances}),
            }
            for member in variant.members
        ],
        "features": [
            {
                "name": feature.name,
                "elements": len(feature.locations),
                "registers": len(feature.registers),
                "instances": feature.instances,
                "members": sorted(feature.shapes),
                "devices": sorted(
                    {devices[device] for member in feature.shapes for device in variant.members[member].devices}
                ),
            }
            for feature in variant.features
        ],
        "renames": sorted({str(rename) for rename in variant.renames}),
        "conflicts": {
            str(other): [str(conflict) for conflict in found]
            for (left, other), found in conflicts.items()
            if left == index and found
        },
        "registers": _register_map(variant, features, mode),
    }


def group_to_json(group: Group) -> dict:
    """:return: the group with all of its variants as a JSON compatible dictionary."""
    devices = {device: index for index, device in enumerate(sorted(group.devices))}
    conflicts = conflicts_between(group.variants, group.mode)
    return {
        "name": group.name,
        "mode": group.mode,
        "ignored": group.ignored,
        "instances": len(group.instances),
        "devices": sorted(devices),
        "variants": [
            _variant(variant, index, devices, conflicts, group.mode) for index, variant in enumerate(group.variants)
        ],
    }


def index_to_json(groups: list[Group]) -> dict:
    """:return: the summary of all groups as a JSON compatible dictionary."""
    return {
        "groups": [
            {
                "name": group.name,
                "ignored": group.ignored,
                "instances": len(group.instances),
                "devices": len(group.devices),
                "shapes": group.shapes,
                "variants": [
                    {
                        "instances": len(variant.instances),
                        "devices": len(variant.devices),
                        "members": len(variant.members),
                        "registers": len({location for location in variant.elements.names if location[0] == "R"}),
                        "core": len(variant.core),
                        "features": len(variant.features),
                    }
                    for variant in group.variants
                ],
            }
            for group in groups
        ],
        "instances": sum(len(group.instances) for group in groups),
        "shapes": sum(group.shapes for group in groups),
        "variants": sum(len(group.variants) for group in groups),
    }


def write_variants(modes: dict[str, list[Group]], path: Path):
    """
    Writes `variants.json` with the summary of every group in every mode and
    `<mode>/<group>.json` with the full register maps of every group.

    :param modes: the analyzed peripheral groups per compatibility mode.
    :param path: the output folder.
    """
    index = {}
    for mode, groups in modes.items():
        folder = path / mode
        folder.mkdir(parents=True, exist_ok=True)
        for group in groups:
            (folder / f"{group.name}.json").write_text(json.dumps(group_to_json(group), separators=(",", ":")) + "\n")
        index[mode] = index_to_json(groups)
    (path / "variants.json").write_text(json.dumps({"modes": index}, indent=0) + "\n")
