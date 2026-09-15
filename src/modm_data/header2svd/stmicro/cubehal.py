# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# CubeHAL Information

The CubeHAL source code complements the CMSIS header with information that
cannot be derived from the header alone:

- The register accesses pair the register members with their bit field macros,
  which is used when the naming heuristics fail, for example, `FW_CR_FPA` for
  `FIREWALL_TypeDef::CR`, or when a register reuses the bit field macros of
  another register, for example, `OPAMP_OTR_TRIMOFFSETN` for `LPOTR`.
- The LL functions document the instances that support a feature with the
  `IS_*_INSTANCE` macros, which restricts the bit fields of the shared
  structure types per instance.
- The LL functions document the enumerated values of the bit fields.
"""

import re
import os
import logging
from pathlib import Path
from collections import defaultdict, Counter

from ..extract import HeaderData, extract_values
from ...cubehal.registers import register_accesses, ll_functions, ll_descriptions

LOGGER = logging.getLogger(__name__)


def _strip_security(name: str) -> str:
    return re.sub(r"_(NS|S)$", "", name)


def _normalize(name: str) -> str:
    return name.replace("_", "").upper()


def _field_macro(identifier: str, bitfields) -> str | None:
    """:return: the bit field macro of a mask identifier resolving aliases."""
    macro = re.sub(r"_(Pos|Msk)$", "", identifier)
    if macro not in bitfields.fields:
        return None
    return bitfields.alias.get(macro, macro)


def _member_types(data: HeaderData) -> dict[str, set[str]]:
    types = defaultdict(set)
    for typedef, members in data.structs.items():
        for member in members:
            types[member.name].add(typedef)
    return types


def register_hints(data: HeaderData, bitfields, path: Path) -> dict[tuple[str, str], str]:
    """
    Pairs the register members with the prefix of the bit field macros that
    are used together in the CubeHAL source code.

    :return: the bit field macro prefix by (typedef, member), e.g. `FW_CR`.
    """
    member_types = _member_types(data)
    instance_types = {_strip_security(name): data.aliases.get(t, t) for name, (t, _) in data.instances.items()}
    macros = defaultdict(set)
    for access in register_accesses(path):
        types = {data.aliases.get(t, t) for t in access.types}
        if access.variable in instance_types:
            types.add(instance_types[access.variable])
        types &= member_types.get(access.member, set())
        # Only use unambiguous accesses
        if len(types) != 1:
            continue
        typedef = types.pop()
        for identifier in access.masks:
            macro = re.sub(r"_(Pos|Msk)$", "", identifier)
            if macro in bitfields.local:
                macros[(typedef, access.member)].add(macro)

    hints = {}
    for (typedef, member), names in macros.items():
        scores = Counter()
        for name in names:
            parts = name.split("_")
            for index in range(2, len(parts)):
                scores["_".join(parts[:index])] += 1

        def score(prefix):
            token = prefix.split("_", 1)[1]
            return (_normalize(token) == _normalize(member), scores[prefix], -len(prefix))

        if scores:
            hints[(typedef, member)] = max(scores, key=score)
    return hints


def instance_restrictions(data: HeaderData, bitfields, path: Path):
    """
    Derives the instances that support a bit field from the `IS_*_INSTANCE`
    macros documented in the LL functions. A bit field that is used by at least
    one function without such a note is supported by all instances.

    :return: (instances by (typedef, member, macro or field name), 32-bit
             counter instances by (typedef, member, macro or field name))
    """
    instances = {name: {_strip_security(i) for i in identifiers} for name, identifiers in data.instance_macros.items()}
    gates = defaultdict(list)
    counters = defaultdict(set)
    for function in ll_functions(path):
        if not function.typedef or function.typedef not in data.structs:
            continue
        # The remap feature is not supported by all instances with the remap registers, e.g. TIM15_TISEL
        macros = {m for m in function.instance_macros if m in instances and m != "IS_TIM_REMAP_INSTANCE"}
        counter = {m for m in macros if "32B_COUNTER" in m}
        macros -= counter
        gate = set().union(*(instances[m] for m in macros)) if macros else None
        counter = set().union(*(instances[m] for m in counter)) if counter else None
        for access in function.accesses:
            keys = {_field_macro(i, bitfields) for i in access.masks} - {None}
            if not access.masks:
                # Registers written as a whole are documented with the field names
                keys = {f for r, f in function.rmtoll if re.sub(r"^\w+_", "", r) == access.member}
            for key in keys:
                gates[(function.typedef, access.member, key)].append(gate)
                if counter is not None:
                    counters[(function.typedef, access.member, key)] |= counter
    restrictions = {}
    for key, usages in gates.items():
        if None not in usages:
            restrictions[key] = set().union(*usages)
    return restrictions, dict(counters)


def _enumeration_name(names: list[str]) -> dict[str, str]:
    """:return: the shortest unique names without the common prefix, e.g. LL_ADC_RESOLUTION_12B -> RESOLUTION_12B."""
    prefix = os.path.commonprefix([name + "_" for name in names])
    prefix = prefix[: prefix.rfind("_") + 1]
    result = {}
    for name in names:
        suffix = name[len(prefix) :]
        if not suffix or not suffix[0].isalpha():
            head = prefix[:-1].rsplit("_", 1)[-1]
            suffix = f"{head}_{suffix}" if suffix else head
        result[name] = suffix
    return result


def enumerations(header: Path, data: HeaderData, bitfields, path: Path) -> dict[str, list[tuple[str, int, str]]]:
    """
    Evaluates the enumerated values of LL functions that access a single bit field.

    :return: the (name, value, description) of the enumerated values by bit field macro.
    """
    candidates = []
    for function in ll_functions(path):
        accesses = [a for a in function.accesses if a.kind in ("MODIFY_REG", "READ_BIT")]
        if len(function.values) < 2 or len(accesses) != 1:
            continue
        macros = {_field_macro(i, bitfields) for i in accesses[0].masks} - {None}
        if len(macros) != 1 or len(bitfields.fields[(macro := macros.pop())]) != 1:
            continue
        candidates.append((macro, function))
    if not candidates:
        return {}

    # Only the preprocessor directives of the LL headers are compiled, since the
    # inline functions do not always compile outside of their intended context
    names = defaultdict(set)
    for _, function in candidates:
        names[function.file].update(function.values)
    sources = {}
    for file, values in names.items():
        text = (path / "Inc" / file).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\\\s*\n", " ", re.sub(r"/\*.*?\*/", "", text, flags=re.S))
        source = "\n".join(line for line in text.splitlines() if line.lstrip().startswith("#"))
        sources[file] = (source, sorted(values & set(re.findall(r"#\s*define\s+(LL_\w+)", source))))
    values = extract_values(header, data.defines, sources, [path / "Inc"])
    descriptions = ll_descriptions(path)

    result = {}
    for macro, function in candidates:
        position, width, _ = bitfields.fields[macro][0]
        mask = ((1 << width) - 1) << position
        evaluated = {name: values[name] for name in function.values if name in values}
        # LL values sometimes encode additional information outside of the bit field
        if len(evaluated) < 2 or any(value & ~mask for value in evaluated.values()):
            continue
        enumeration = result.setdefault(macro, [])
        seen = {v for _, v, _ in enumeration}
        shortnames = _enumeration_name(list(evaluated))
        for name, value in evaluated.items():
            if (value := value >> position) not in seen:
                seen.add(value)
                enumeration.append((shortnames[name], value, descriptions.get(name, "")))
    return result
