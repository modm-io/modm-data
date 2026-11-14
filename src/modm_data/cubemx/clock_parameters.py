# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Clock Tree Parameters

The RCC parameters of the CubeMX modes files and the declarations of
their domains and limits that hold for a device.
"""

from collections import defaultdict
from functools import cache

from ..utils import XmlReader
from . import conditions
from .device_data import _MCU_PATH


@cache
def read_parameters(rcc_version: str) -> dict[str, list[dict]]:
    """
    A parameter may be declared several times, each declaration guarded by a
    condition. Some of those conditions describe the device, and some describe
    the configuration, which makes the parameter derived, see `select`.

    :param rcc_version: The `Version` of the RCC IP of the device.
    :return: Every declaration of every parameter, in file order, by name.
    """
    modes = XmlReader(_MCU_PATH / "IP" / f"RCC-{rcc_version}_Modes.xml")
    parameters = defaultdict(list)
    for element in modes.query("//RefParameter"):
        name = element.get("Name")
        if not name:
            continue
        declaration = {"type": element.get("Type", ""), "default": element.get("DefaultValue", "")}
        for key, out in (("Min", "min"), ("Max", "max"), ("Unit", "unit"), ("IP", "ip")):
            if (value := element.get(key)) is not None:
                declaration[out] = value
        if values := element.findall("PossibleValue"):
            declaration["values"] = [
                {
                    "macro": value.get("Value", ""),
                    "label": value.get("Comment", ""),
                    # A bare identifier in a condition is a semaphore, which is
                    # true when its parameter stands at the value declaring it
                    **({"semaphore": value.get("Semaphore")} if value.get("Semaphore") else {}),
                    # A value the configuration rules out, for example, a voltage scale too slow for SYSCLK
                    **({"disabled": True} if value.get("Action") == "Disable" else {}),
                }
                for value in values
            ]
        declaration["when"] = [c.get("Expression") for c in element.findall("Condition") if c.get("Expression")]
        parameters[name].append(declaration)
    # The ADC clock starts at 0.14 MHz, not at 1.4 MHz as the STM32L4, STM32L4+,
    # STM32WB1 and STM32WL files say: DS10198, DS11585 and DS11451 6.3.18,
    # DS13258 6.3.19 and DS13293 5.3.19 ADC characteristics (DS12023 gives no minimum)
    for declaration in parameters.get("ADCFreq_Value", ()):
        if declaration.get("min") == "1400000":
            declaration["min"] = "140000"
    return dict(parameters)


def _default(value: str) -> float:
    """
    A value is usually a number, but it can also be a formula over other
    parameters, for example, `=RTCFreq_Value*4` or `=SYSCLKFreq_VALUE/2`.

    :return: The number, or None when the value is a formula.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def select(declarations: list[dict], context: dict) -> tuple[dict, list]:
    """
    Drops the declarations that the device rules out, then decides whether what
    remains is one value or a set of rules to evaluate against the configuration.

    Several declarations with the same fixed value and configuration dependent
    guards are a *derived* parameter, the STM32G0 timer prescaler being the
    canonical one:

        <RefParameter Name="APB1TimCLKDivider" Min="1" Max="1">
            <Condition Expression="APB1CLKDivider=RCC_HCLK_DIV1"/>
        <RefParameter Name="APB1TimCLKDivider" Min="2" Max="2">

    which is the usual rule that the timer clock is the APB clock when the APB
    prescaler is one and twice the APB clock otherwise.

    :param declarations: Every declaration of one parameter, in file order.
    :param context: The device properties, see `modm_data.cubemx.conditions.context_of`.
    :return: The declaration to use and the rules, which are empty unless the
             parameter is derived.
    """
    live = [d for d in declarations if all(conditions.holds(gate, context) for gate in d["when"])]
    if not live:
        live = declarations[:1]
    # A guarded declaration overrides the bare one, so when several survive only
    # because their guard is unresolvable, the bare one is the safer answer. The
    # STM32F102 file caps SYSCLK at 36 MHz for the STM32F101, 48 for the F102
    # and 72 for everything else, and an STM32F103 must land on the last of those.
    if len(live) > 1:
        certain = [
            d
            for d in live
            if d["when"] and all(conditions.evaluate(conditions.parse(g), context) is True for g in d["when"])
        ]
        if certain:
            live = certain + [d for d in live if d not in certain]
        elif bare := [d for d in live if not d["when"]]:
            live = bare + [d for d in live if d["when"]]
    fixed = [d for d in live if d.get("min") and d["min"] == d.get("max") and _default(d["min"]) is not None]
    if len(live) > 1 and len(fixed) == len(live):
        rules = []
        for declaration in live:
            rule = {"factor": _default(declaration["min"])}
            if declaration["when"]:
                try:
                    gates = [conditions.to_json(conditions.parse(gate), context) for gate in declaration["when"]]
                except Exception:
                    return live[0], []
                rule["when"] = gates[0] if len(gates) == 1 else ["and", *gates]
            rules.append(rule)
        # Only useful if the rules actually disagree, and one must be the default
        if len({rule["factor"] for rule in rules}) > 1 and any("when" not in r for r in rules):
            return live[0], rules
    return live[0], []


def _envelope(variants: list[dict], prefix: str) -> dict:
    """
    The static limits span the variants: a bound only if every variant with
    limits has it, the F0 APB1 minimum of 10 MHz holds only for the USB, and
    a variant without any limit stands for an element not in use.
    """
    bounded = [v for v in variants if prefix + "min" in v or prefix + "max" in v]
    static = {}
    for bound, pick in (("min", min), ("max", max)):
        if bounded and all(prefix + bound in v for v in bounded):
            static[prefix + bound] = pick(v[prefix + bound] for v in bounded)
    return static


def limits(declarations: list[dict], context: dict) -> tuple[dict, list]:
    """
    The frequency limits of a signal often depend on the configuration, most of
    all on the voltage scale, and the declaration without a condition is the
    fallback for when the element is not in use:

        <RefParameter Name="VCOOutputFreq_Value" Min="96000000" Max="344000000">
            <Condition Expression="scale1 &amp; (PLLUsed=1)"/>
        <RefParameter Name="VCOOutputFreq_Value" Min="96000000" Max="128000000">

    The first declaration whose condition is not false applies, so an unknown
    condition like `PLLUsed=1` counts as satisfied: the limit matters only
    while the element is in use anyway.

    :return: The static limits, which span all variants that have one, so
             what the device can do in its best configuration, and the
             variants in file order, which are empty unless they disagree.
    """
    variants = []
    for declaration in declarations:
        if not all(conditions.holds(gate, context) for gate in declaration["when"]):
            continue
        variant = {k: v for k in ("min", "max") if k in declaration and (v := _default(declaration[k])) is not None}
        if declaration["when"]:
            try:
                gates = [conditions.to_json(conditions.parse(gate), context) for gate in declaration["when"]]
            except Exception:
                continue
            variant["when"] = gates[0] if len(gates) == 1 else ["and", *gates]
        variants.append(variant)
        if "when" not in variant:
            break
    if not variants:
        return {}, []
    static = _envelope(variants, "")
    if len({(v.get("min"), v.get("max")) for v in variants}) < 2:
        return static, []
    return static, variants
