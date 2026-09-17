# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Naming a Feature

A feature is a set of elements that appear in exactly the same instances, which
is a mechanical definition and says nothing about what the feature *is*. Most
of them do have something in common though, since ST names and documents
related elements alike, so the name is derived from whatever the elements share:

- a single element simply uses its own name and description,
- `RCC.DACEN`, `RCC.DACRST` and `RCC.DACLPEN` share the name prefix `DAC`,
- `EXTI.RT27` and `EXTI.FT27` share the description "trigger ... on line 27",
- `DMA.CCR4` … `DMA.CCR6` share the register name prefix `CCR`.

About four out of five features are named this way. The rest are genuinely
heterogeneous, mostly large `RCC` and `DMA` features that enable a whole set of
unrelated peripherals or channels at once, and fall back to a plain count.
"""

import re

Element = tuple[str, str, str, str]
"""kind (`R` or `F`), register name, element name, description."""

_WORDS = re.compile(r"[A-Za-z0-9/]+")
# Words that carry no meaning on their own and make for a poor feature name
_FILLER = {"the", "and", "for", "bit", "bits", "register", "registers", "this", "not", "used"}


def _common_prefix(names: list[str]) -> str:
    head, tail = min(names), max(names)
    length = 0
    while length < len(head) and length < len(tail) and head[length] == tail[length]:
        length += 1
    return head[:length].rstrip("_")


def _common_phrase(descriptions: list[str]) -> str:
    """:return: the longest run of words that appears in every description."""
    words = [_WORDS.findall(description) for description in descriptions]
    if not all(words):
        return ""
    shortest = min(words, key=len)
    best = []
    for start in range(len(shortest)):
        for end in range(len(shortest), start + len(best), -1):
            phrase = [word.lower() for word in shortest[start:end]]
            if all(
                any(
                    phrase == [w.lower() for w in other[i : i + len(phrase)]]
                    for i in range(len(other) - len(phrase) + 1)
                )
                for other in words
            ):
                best = shortest[start:end]
                break
    return " ".join(best).strip(" ,.:;-")


def name_feature(elements: list[Element], names: dict[str, str] = None) -> str:
    """
    :param elements: the elements of one feature.
    :param names: the lookup table of generated names, see
                  `modm_data.svd2variants.annotate`, which takes precedence.
    :return: a short human readable name, see the module documentation.
    """
    if not elements:
        return ""
    if names:
        from .annotate import feature_key

        if generated := names.get(feature_key(elements)):
            return generated
    names = [element[2] for element in elements]
    registers = sorted({element[1] for element in elements})
    descriptions = [element[3] for element in elements if element[3]]

    if len(elements) == 1:
        kind, register, name, description = elements[0]
        label = name if kind == "R" else f"{register}.{name}"
        return f"{label} — {description}" if description else label

    if len(set(names)) == 1:
        return f"{names[0]} in {len(registers)} registers"

    phrase = _common_phrase(descriptions) if len(descriptions) == len(elements) else ""
    if len(phrase.split()) >= 2:
        return phrase

    if len(prefix := _common_prefix(names)) >= 3:
        return f"{prefix}*"

    if len(registers) == 1:
        return f"{registers[0]}: {len(elements)} bit fields"

    if len(registers) > 1 and len(prefix := _common_prefix(registers)) >= 3:
        return f"{prefix}* in {len(registers)} registers"

    if len(registers) <= 3:
        return ", ".join(registers)

    # A single shared word is a weak name, but still better than a bare count
    if len(phrase) >= 4 and phrase.lower() not in _FILLER:
        return f"{phrase} in {len(registers)} registers"

    return f"{len(elements)} elements in {len(registers)} registers"
