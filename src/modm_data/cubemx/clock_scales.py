# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Clock Tree Voltage Scales

The voltage scale and over-drive limits and the flash wait states of a
clock tree.
"""

import itertools
import re
from collections import defaultdict

from ..kg.stmicro import did_from_string
from . import conditions, stm32_data
from ..clocks.evaluate import _SCALED, _scale_rank
from .clock_parameters import _envelope


# The clocks whose maximum depends on the voltage scale


def _thresholds(declarations: list[dict], variable: str) -> set[float]:
    """:return: The numbers that the conditions of the declarations compare the variable with."""
    numbers, stack = set(), [conditions.parse(g) for d in declarations for g in d["when"]]
    while stack:
        node = stack.pop()
        if node[0] in ("eq", "cmp") and node[1] == variable:
            try:
                numbers.add(float(node[2] if node[0] == "eq" else node[3]))
            except ValueError:
                pass
        elif node[0] in ("and", "or", "not"):
            stack += node[1:]
    return numbers


# The parameters of the regulator that bound the core clocks
_REGULATORS = ("PWR_Regulator_Voltage_Scale", "PWREXT_OverDrive")


def scale_limits(parameters: dict, context: dict) -> dict[str, list[tuple[dict, float]]]:
    """
    CubeMX does not bound the clocks by the voltage scale directly, but it
    knows the limits through other parameters: the flash latency, whose
    declarations hold up to a frequency per scale,

        <RefParameter Name="FLatency" DefaultValue="FLASH_LATENCY_4">
            <Condition Expression="(scale1 &amp; ((HCLKFreq_Value &lt; 150000000)|(HCLKFreq_Value= 150000000)))"/>

    and the regulator parameters themselves, the voltage scale and, on the
    STM32F4 and STM32F7, the over-drive, whose declarations list the values a
    band of frequencies allows, the first declaration that holds deciding:

        <RefParameter Name="PWR_Regulator_Voltage_Scale">
            <Condition Expression="(SYSCLKFreq_VALUE &lt;= 150000000) &amp; (SYSCLKFreq_VALUE &gt; 100000000)"/>
            <PossibleValue Action="Disable" Value="PWR_REGULATOR_VOLTAGE_SCALE3"/>
        <RefParameter Name="PWR_Regulator_Voltage_Scale">
            <Condition Expression="(PWREXT_OverDrive=PWREXT_OverDrive_DESACTIVATED) &amp; (HCLKFreq_Value &gt;= 144000000)"/>
            <PossibleValue Value="PWR_REGULATOR_VOLTAGE_SCALE1"/>

    Every combination of regulator values is sampled at every number the
    declarations compare a clock with, with all core clocks at that frequency
    and each system clock source in turn, since the STM32L5 and STM32WL pick
    the scale by the source too. The highest frequency that a combination
    allows with any source is its limit, and a limit keeps only the regulator
    values it depends on.

    :return: The limits per clock parameter, each as the regulator values it
             holds for and the maximum frequency.
    """

    def live(name):
        return [d for d in parameters.get(name, ()) if all(conditions.holds(g, context) for g in d["when"])]

    regulators = {r: sorted({v["macro"] for d in live(r) for v in d.get("values", ())}) for r in _REGULATORS}
    regulators = {r: values for r, values in regulators.items() if len(values) > 1}
    if not regulators:
        return {}
    # The semaphores of the scales and of the system clock sources, as the conditions spell them
    flags = {
        v["semaphore"]: v["macro"] for r in regulators for d in live(r) for v in d.get("values", ()) if "semaphore" in v
    }
    sources = sorted({v["semaphore"] for d in live("SYSCLKSource") for v in d.get("values", ()) if "semaphore" in v})
    assignments = [dict(zip(regulators, values)) for values in itertools.product(*regulators.values())]

    caps = defaultdict(dict)
    for name in ("FLatency", *regulators):
        declarations = live(name)
        if name == "FLatency":
            # Only the declarations per scale, the others test the supply
            declarations = [d for d in declarations if any(re.search(rf"\b{f}\b", g) for f in flags for g in d["when"])]
        variables = [v for v in _SCALED if _thresholds(declarations, v)]
        if not variables:
            continue
        points = set().union(*(_thresholds(declarations, v) for v in variables))
        # Just below and at every threshold, and well beyond the last one
        samples = sorted({*points, *(p - 1 for p in points), max(points) * 2})
        parsed = [[conditions.parse(g) for g in d["when"]] for d in declarations]

        def allowed(x: float, assignment: dict, source: str) -> bool:
            values = {**context, **assignment, **dict.fromkeys(_SCALED + ("HCLK3Freq_Value",), x)}
            values |= {f: m in assignment.values() for f, m in flags.items()} | {s: s == source for s in sources}
            for declaration, gates in zip(declarations, parsed):
                results = [conditions.evaluate(g, values) for g in gates]
                if None in results or False in results:
                    # Undecidable counts as not holding, another source may decide it
                    continue
                if name == "FLatency":
                    return True
                return any(v["macro"] == assignment[name] and not v.get("disabled") for v in declaration["values"])
            return name != "FLatency"

        for index, assignment in enumerate(assignments):
            passed = [x for x in samples if any(allowed(x, assignment, s) for s in sources or [None])]
            # Never allowed means the parameter does not test the clocks for these values
            if passed and passed[-1] != samples[-1]:
                # An open bound like HCLK < 144 MHz passes just below, which is the threshold
                cap = passed[-1] + 1 if passed[-1] + 1 in points else passed[-1]
                for variable in variables:
                    caps[variable][index] = min(caps[variable].get(index, cap), cap)

    result = {}
    for variable, limits in caps.items():
        # The fewest regulators that the limits depend on, the voltage scale first
        for size in range(len(regulators) + 1):
            for subset in itertools.combinations(regulators, size):

                def key(i):
                    return tuple(assignments[i][r] for r in subset)

                groups = defaultdict(set)
                for index in range(len(assignments)):
                    groups[key(index)].add(limits.get(index))
                if all(len(g) == 1 for g in groups.values()):
                    break
            else:
                continue
            break
        result[variable] = [
            (dict(zip(subset, values)), next(iter(cap))) for values, cap in sorted(groups.items()) if None not in cap
        ]
    return result


def flash_latency(parameters: dict, context: dict) -> list[dict]:
    """
    The flash wait states are a parameter of their own, declared once per
    number of wait states with the frequencies it holds for, in order:

        <RefParameter Name="FLatency" DefaultValue="FLASH_LATENCY_1">
            <Condition Expression="(scale1 &amp; ((HCLKFreq_Value &lt; 60000000)|(HCLKFreq_Value= 60000000)))"/>
            <PossibleValue Value="FLASH_LATENCY_1"/>

    :return: The wait states macro and its condition, the first that holds is the fewest.
    """
    latencies = []
    for declaration in parameters.get("FLatency", ()):
        if not declaration["when"] or not all(conditions.holds(g, context) for g in declaration["when"]):
            continue
        values = [v["macro"] for v in declaration.get("values", ())]
        try:
            gates = [conditions.to_json(conditions.parse(g), context) for g in declaration["when"]]
        except Exception:
            continue
        latencies.append(
            {
                "latency": values[0] if len(values) == 1 else declaration["default"],
                "when": gates[0] if len(gates) == 1 else ["and", *gates],
            }
        )
    return latencies


def manual_flash_latency(partname: str, parameters: dict) -> list[dict]:
    """
    The flash wait states from the tables of `stm32_data`, which were derived
    from the CubeMX `FLatency` declarations and corrected with the reference
    manuals, since CubeMX misses some: the first wait state of the STM32H7A3,
    STM32H7R/S and STM32U3, the 72 MHz of the STM32F1/F3, all of the STM32F100,
    STM32WB0 and STM32WL3. The tables are keyed by the minimum core voltage per
    voltage scale, or by the supply voltage on the STM32F2/F4/F7.

    :return: The wait states macro and its condition like `flash_latency`, or
             nothing if the table does not fit the voltage scales of the device.
    """
    did = did_from_string(partname.lower())
    table = stm32_data.getFlashLatencyForDevice(did)
    if not table:
        return []
    voltages = sorted(table, reverse=True)
    if len(voltages) == 1:
        gates = [None]
    elif did.family in ("f2", "f4", "f7"):
        gates = [["cmp", "VDD_VALUE", ">=", f"{mv / 1000:g}"] for mv in voltages]
    else:
        scales = {v["macro"] for d in parameters.get("PWR_Regulator_Voltage_Scale", ()) for v in d.get("values", ())}
        scales = sorted(scales, key=_scale_rank)
        if len(scales) != len(voltages):
            return []
        gates = [["eq", "PWR_Regulator_Voltage_Scale", scale] for scale in scales]
    latencies = []
    for gate, mv in zip(gates, voltages):
        for states, limit in enumerate(table[mv]):
            when = ["cmp", "HCLKFreq_Value", "<=", str(limit)]
            latencies.append({"latency": f"FLASH_LATENCY_{states}", "when": ["and", gate, when] if gate else when})
    return latencies


def _add_limit(node: dict, key: str, variant: dict):
    """Puts a limit before the others of a node, so it wins whenever its condition holds."""
    prefix = "out_" if key == "out_limits" else ""
    if key not in node:
        static = {k: node[k] for k in (f"{prefix}min", f"{prefix}max") if k in node}
        node[key] = [static] if static else []
    node[key].insert(0, variant)
    # The static limits span the variants again
    for bound in ("min", "max"):
        node.pop(prefix + bound, None)
    node.update(_envelope(node[key], prefix))


def add_scale_limits(nodes: dict, limits: dict[str, list[tuple[dict, float]]]):
    """Bounds the clock nodes by the regulator limits, see `scale_limits`."""
    by_param = {node["param"]: name for name, node in nodes.items() if "param" in node}
    for variable, variants in limits.items():
        if (name := by_param.get(variable)) is None:
            continue
        node = nodes[name]
        for values, limit in sorted(variants, key=lambda item: -item[1]):
            if limit < node.get("max", float("inf")) and values:
                gates = [["eq", r, v] for r, v in values.items()]
                _add_limit(node, "limits", {"max": limit, "when": gates[0] if len(gates) == 1 else ["and", *gates]})
