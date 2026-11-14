# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# STM32 Clock Tree

The clock tree of a device as a graph of nodes that a constraint solver can
enumerate: every node carries the factors it can take, the limits it must obey
and the inputs it can take them from, so that a configuration can be searched
for instead of copied from the reference manual.

Three sources describe one tree, since none of them is complete:

- The CubeMX clock plugin `plugins/clock/<Tree>.xml` draws the topology, which
  elements exist and what feeds what, for a whole family at once.
- The RCC modes file `mcu/IP/RCC-<version>_Modes.xml` declares the parameters
  the elements refer to, their domains and their frequency limits.
- The CMSIS device header names the peripherals on each bus in the RCC clock
  enable registers, which is what the tree leaves out: it only muxes the
  peripherals that have a clock source of their own and terminates everything
  else in a bus output.

The family files describe every device of that family, so the elements and the
parameter declarations are gated by conditions that the MCU description
answers, see `modm_data.cubemx.conditions`.
"""

import difflib
import logging
import re
from functools import cache

from ..utils import XmlReader
from . import conditions
from ..clocks.evaluate import _number, evaluate  # noqa: F401
from .device_data import _MCU_PATH, _device_file, header_from_partname
from .clock_corrections import correct
from .clock_parameters import _default, limits, read_parameters, select
from .clock_peripherals import attach_peripherals, base_buses, header_buses, peripherals_of
from .clock_scales import add_scale_limits, flash_latency, manual_flash_latency, scale_limits

LOGGER = logging.getLogger(__name__)

_CLOCK_PATH = _MCU_PATH.parent / "plugins" / "clock"

# The element types that carry a frequency, everything else only draws the picture
_KINDS = {
    "fixedSource": "fixed",
    "variedSource": "varied",
    "distinctValsSource": "choice",
    "multiplexor": "mux",
    "xbar": "mux",
    "devisor": "div",
    "multiplicator": "mul",
    "fractional": "frac",
    "multiplicatorFrac": "mulfrac",
    "output": "output",
    "activeOutput": "output",
}


# =================================== TREE ====================================
def _score(gates: list[str], context: dict) -> int:
    """
    How well a declaration fits this device, so that the most specific one wins:
    a condition the device satisfies beats one it cannot answer, and a
    declaration without a condition is the fallback. The STM32G0 draws PLLN
    twice, once with the PLLQ output for `!STM32G0x0_Value_line` and once
    without it for everything else, and an STM32G071 must take the first.
    """
    if not gates:
        return 1
    if all(conditions.evaluate(conditions.parse(gate), context) is True for gate in gates):
        return 2
    return 0


def read_tree(drawing: str, context: dict) -> tuple[dict, int]:
    """
    An element may be declared several times, once per die, package or product
    line that draws it differently, so the conditions decide which declaration
    this device gets, see `_score`.

    :param drawing: The `ClockTree` of the device, for example, `STM32G0`.
    :param context: The device properties, see `modm_data.cubemx.conditions.context_of`.
    :return: The elements of the clock tree and how many were dropped.
    """
    plugin = XmlReader(_CLOCK_PATH / f"{drawing}.xml")
    nodes, scores, dropped = {}, {}, 0
    for element in plugin.query("//Element"):
        kind = _KINDS.get(element.get("type", ""))
        if kind is None:
            continue
        gates = [c.get("Expression") for c in element.findall("Condition") if c.get("Expression")]
        if not all(conditions.holds(gate, context) for gate in gates):
            dropped += 1
            continue
        node = {
            "kind": kind,
            "inputs": [
                {
                    "from": i.get("from", ""),
                    "signal": i.get("signalId", ""),
                    **({"macro": i.get("refValue")} if i.get("refValue") else {}),
                }
                for i in element.findall("Input")
            ],
            "outputs": sorted({o.get("to", "") for o in element.findall("Output")}),
            "signals": sorted({o.get("signalId") for o in element.findall("Output") if o.get("signalId")}),
        }
        # CubeMX draws the tree itself, so the coordinates are the real ST layout
        for axis in ("x", "y"):
            if (position := element.get(axis)) is not None:
                node[axis] = float(position)
        if parameter := element.get("refParameter"):
            node["param"] = parameter
        if enable := element.get("refEnable"):
            node["enable"] = enable
        if gates:
            node["when"] = gates
        name = element.get("id")
        score = _score(gates, context)
        if name in nodes and scores[name] >= score:
            dropped += 1
            continue
        nodes[name], scores[name] = node, score

    # An element may name an edge on one side only, and the drawing needs both,
    # but not to the elements this device does not have, like the F2 PLLSAIN
    for node in nodes.values():
        node["outputs"] = [o for o in node["outputs"] if o in nodes]
    for name, node in nodes.items():
        for input in node["inputs"]:
            source = nodes.get(input["from"])
            if source is not None and name not in source["outputs"]:
                source["outputs"] = sorted({*source["outputs"], name})
    return nodes, dropped


@cache
def read_signals(drawing: str) -> dict[str, str]:
    """
    :return: The parameter that bounds each signal of the drawing, for example,
             `PLLCLK` -> `PLLRCLKFreq_Value` on the STM32G0.
    """
    plugin = XmlReader(_CLOCK_PATH / f"{drawing}.xml")
    signals = {}
    for signal in plugin.query("//Signals/Signal"):
        # Some name a second parameter or null after a comma, the first one bounds it
        if (name := signal.get("id")) and (parameter := (signal.get("refParameter") or "").split(",")[0].strip()):
            signals[name] = parameter
    return signals


@cache
def _modes_xml(rcc_version: str) -> XmlReader:
    return XmlReader(_MCU_PATH / "IP" / f"RCC-{rcc_version}_Modes.xml")


def read_modes(rcc_version: str, context: dict) -> dict[str, dict[str, str]]:
    """
    The oscillators run in one of several modes that the pin configuration
    chooses, not a parameter, and some limits test them, for example, the HSE
    in bypass mode on the STM32G0 runs up to 48 MHz, but only up to 26 MHz in
    Range 2:

        <Mode Name="OSC" UserName="High Speed Clock (HSE)">
            <ModeLogicOperator Name="XOR">
                <Mode Name="HSE-External-Clock-Source"><Semaphore>HSEByPass</Semaphore></Mode>
                <Mode Name="HSE-External-Oscillator"><Semaphore>HSEOscillator</Semaphore></Mode>

    :return: The semaphore of each mode by its short name, per oscillator, for
             example, `{"HSE": {"ByPass": "HSEByPass", "Oscillator": "HSEOscillator"}}`.
    """
    modes = {}
    for parent in _modes_xml(rcc_version).query('//Mode[ModeLogicOperator[@Name="XOR"]/Mode/Semaphore]'):
        if (remove := parent.get("RemoveCondition")) and conditions.evaluate(conditions.parse(remove), context) is True:
            continue
        name = re.search(r"\((\w+)\)", parent.get("UserName", "")) or None
        name = name[1] if name else parent.get("Name", "")
        for mode in parent.findall("ModeLogicOperator/Mode"):
            for semaphore in mode.findall("Semaphore"):
                flag = semaphore.text.strip()
                short = flag[len(name) :] if flag.startswith(name) and flag != name else flag
                modes.setdefault(name, {}).setdefault(short, flag)
    return modes


def _mux_default(node: dict, default: str) -> str:
    """
    The default of a mux is not always one of its inputs: the STM32G0 defaults
    USART1 to `RCC_USART1CLKSOURCE_PCLK2` but names the input
    `RCC_USART1CLKSOURCE_PCLK1`, since it has only one APB.

    :return: The default, or the input macro closest to it.
    """
    macros = [i["macro"] for i in node["inputs"] if i.get("macro")]
    if not macros or default in macros:
        return default
    close = difflib.get_close_matches(default, macros, n=1, cutoff=0.8)
    return close[0] if close else macros[0]


def resolve(nodes: dict, parameters: dict, context: dict, signals: dict | None = None):
    """
    Fills in the numeric domain and the limits of every node from its parameter.

    :param signals: The parameter of each signal, see `read_signals`, and else
                    the one named after the signal, `<signal>Freq_Value`.
    """
    for node in nodes.values():
        # Some elements name two parameters, for example, the STM32F4 RTC mux
        # RCC_RTC_Clock_Source,RCC_RTC_Clock_SourceVirtual, and the first declared one counts
        if "," in node.get("param", ""):
            names = [p.strip() for p in node["param"].split(",")]
            node["param"] = next((p for p in names if p in parameters), names[0])
        declarations = parameters.get(node.get("param", ""))
        if declarations is None:
            continue
        parameter, rules = select(declarations, context)
        if rules:
            node["rules"] = rules
        if node["kind"] in ("fixed", "varied", "output"):
            # The limits of a frequency, which may depend on the voltage scale
            static, variants = limits(declarations, context)
            node.update(static)
            if variants:
                node["limits"] = variants
        else:
            for key in ("min", "max"):
                # A limit can be a formula over other parameters rather than a number
                if key in parameter and (limit := _default(parameter[key])) is not None:
                    node[key] = limit
        if node["kind"] in ("fixed", "varied"):
            number = _default(parameter["default"])
            key = "value" if node["kind"] == "fixed" else "default"
            if number is None:
                node["expr"] = parameter["default"]
                node[key] = 0.0
            else:
                node[key] = number
        elif node["kind"] == "choice":
            # The MSI ranges are labeled in their unit, for example, 100 KHz
            scale = {"khz": 1e3, "mhz": 1e6}.get(parameter.get("unit", "").lower(), 1)
            domain = [
                {"value": _number(v["label"]) * scale, "macro": v["macro"]}
                for v in parameter.get("values", ())
                if _number(v["label"]) is not None
            ]
            node["domain"] = domain
            if found := [d for d in domain if d["macro"] == parameter["default"]]:
                node["default"] = found[0]["value"]
            elif (number := _default(parameter["default"])) is not None:
                # The STM32U3 MSIK defaults to 96000000, not to its macro
                node["default"] = number
        elif node["kind"] in ("div", "mul", "mulfrac", "frac"):
            if values := parameter.get("values"):
                domain = [{"factor": _number(v["label"]), "macro": v["macro"]} for v in values]
                node["domain"] = [d for d in domain if d["factor"]]
            elif parameter["type"] == "integer" and parameter.get("min") and parameter.get("max"):
                node["range"] = [int(float(parameter["min"])), int(float(parameter["max"]))]
            elif (only := _default(parameter["default"])) is not None:
                # A list with no values and a numeric default is a fixed factor, for example, HSE_Div2
                node["domain"] = [{"factor": only, "macro": parameter["default"]}]
            if (number := _default(parameter["default"])) is not None:
                node["default"] = number
            elif found := [d for d in node.get("domain", ()) if d["macro"] == parameter["default"]]:
                node["default"] = found[0]["factor"]
        elif node["kind"] == "mux":
            # The refValue already sits on each input, only the default is missing
            node["default"] = _mux_default(node, parameter.get("default", ""))

    # The PLL limits hang off the signal, not the element, for example, the
    # VCOInput of PLLM is bounded by VCOInputFreq_Value, not by the PLLM parameter
    for node in nodes.values():
        for signal in node.get("signals", ()):
            parameter = (signals or {}).get(signal, f"{signal}Freq_Value")
            # A source is bounded by its own parameter already
            if parameter == node.get("param") or (declarations := parameters.get(parameter)) is None:
                continue
            # The frequency some derived options test, for example, VCOInputFreq_Value for the STM32H5 VCI range
            node.setdefault("signal_param", parameter)
            static, variants = limits(declarations, context)
            for key, limit in static.items():
                node.setdefault(f"out_{key}", limit)
            if variants:
                node.setdefault(
                    "out_limits",
                    [{(f"out_{k}" if k != "when" else k): v for k, v in variant.items()} for variant in variants],
                )


def bus_peripherals(parameters: dict, nodes: dict) -> dict[str, list[str]]:
    """
    :return: The peripherals of every bus output node, which the CubeMX
             parameter of the node lists in its `IP` attribute.
    """
    buses = {}
    for name, node in nodes.items():
        declarations = parameters.get(node.get("param", ""))
        if node["kind"] != "output" or declarations is None:
            continue
        if not (ip := declarations[0].get("ip")):
            continue
        peripherals = sorted({p.strip() for p in ip.split(",") if p.strip()})
        # A single peripheral is that peripheral's own output, not a bus
        if len(peripherals) > 1:
            buses[name] = peripherals
    return buses


# ================================== DEVICE ===================================
def _semaphores(parameters: dict) -> dict[str, dict]:
    """:return: The parameter and value that each semaphore identifier stands for."""
    semaphores = {}
    for name, declarations in parameters.items():
        for declaration in declarations:
            for value in declaration.get("values", ()):
                if flag := value.get("semaphore"):
                    semaphores.setdefault(flag, {"param": name, "macro": value["macro"]})
    return semaphores


def _options(nodes: dict, parameters: dict, semaphores: dict, extra: list = ()) -> dict[str, str]:
    """:return: The parameters that a derivation rule or the `extra` conditions test but no node stands for."""
    wanted, bound = set(), {node["param"] for node in nodes.values() if "param" in node}
    stack = [
        rule.get("when")
        for node in nodes.values()
        for key in ("rules", "limits", "out_limits")
        for rule in node.get(key, ())
    ] + [e["when"] for e in extra]
    while stack:
        gate = stack.pop()
        if not isinstance(gate, list):
            continue
        if gate[0] in ("eq", "cmp"):
            wanted.add(gate[1])
        elif gate[0] == "var" and gate[1] in semaphores:
            wanted.add(semaphores[gate[1]]["param"])
        stack += gate[1:]
    options = {}
    for name in sorted(wanted - bound):
        if name not in parameters:
            continue
        # The declaration without a condition holds the default, the others the default under that condition
        default = next((d["default"] for d in parameters[name] if not d["when"]), parameters[name][0]["default"])
        if not default:
            # The STM32U5 and STM32WB leave the voltage scale empty, so take the
            # fastest, which is the lowest number, PWR_REGULATOR_VOLTAGE_SCALE1
            values = [v["macro"] for d in parameters[name] for v in d.get("values", ())]

            def number(m):
                return int(found[0]) if (found := re.search(r"\d+$", m)) else 1 << 16

            default = min(values, key=number) if values else ""
        options[name] = default
    return options


def _derived(options: dict, parameters: dict, context: dict) -> dict[str, list[dict]]:
    """
    Some options are derived from the configuration, the first declaration
    that holds giving the value, for example, the main PLL is in use if it
    clocks SYSCLK, and the STM32F446 activates the over-drive above 168 MHz:

        <RefParameter Name="PLLUsed" DefaultValue="1">
            <Condition Expression="(SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK)|..."/>
        <RefParameter Name="PLLUsed" DefaultValue="0"/>

    :return: The values with their conditions per option, in file order.
    """
    derived = {}
    for name in options:
        values = []
        for declaration in parameters[name]:
            if not all(conditions.holds(g, context) for g in declaration["when"]):
                continue
            value = {"value": declaration["default"]}
            if declaration["when"]:
                gates = [conditions.to_json(conditions.parse(g), context) for g in declaration["when"]]
                value["when"] = gates[0] if len(gates) == 1 else ["and", *gates]
            values.append(value)
            if "when" not in value:
                break
        if len({v["value"] for v in values}) > 1:
            derived[name] = values
    return derived


def clock_tree_from_partname(partname: str) -> dict:
    """
    The clock tree of one device as a solver friendly graph.

    :param partname: A full STM32 device name.
    :return: The nodes of the tree with the peripherals attached to them, or
             None if the device has no clock tree in the database.
    """
    device_file = _device_file(partname)
    drawing = device_file.tree.get("ClockTree")
    rcc = next((ip.get("Version") for ip in device_file.query('//IP[@Name="RCC"]')), None)
    if not drawing or not rcc:
        LOGGER.error(f"No clock tree found for {partname}")
        return None
    if not (_CLOCK_PATH / f"{drawing}.xml").exists() or not (_MCU_PATH / "IP" / f"RCC-{rcc}_Modes.xml").exists():
        LOGGER.error(f"Clock tree files missing for {partname}: {drawing}, RCC-{rcc}")
        return None

    parameters = read_parameters(rcc)
    context = conditions.context_of(device_file)
    nodes, dropped = read_tree(drawing, context)
    resolve(nodes, parameters, context, read_signals(drawing))
    buses = bus_peripherals(parameters, nodes)

    instances = peripherals_of(device_file)
    header = header_from_partname(partname)
    attached = attach_peripherals(
        nodes, buses, header_buses(header) if header else {}, instances, base_buses(header) if header else {}
    )

    semaphores = _semaphores(parameters)
    add_scale_limits(nodes, scale_limits(parameters, context))
    latencies = manual_flash_latency(partname, parameters) or flash_latency(parameters, context)
    options = _options(nodes, parameters, semaphores, latencies)
    constraints = correct(nodes, options, partname)
    # A limit an output of a fixed source always meets says nothing, the STM32G4 IWDG runs at LSI, not up to 170 MHz
    for node in nodes.values():
        sources = [nodes.get(i["from"], {}) for i in node["inputs"]]
        if node["kind"] == "output" and len(sources) == 1 and sources[0].get("kind") == "fixed":
            value = sources[0].get("value", 0)
            if node.get("min", 0) <= value <= node.get("max", value) and "limits" not in node:
                node.pop("min", None)
                node.pop("max", None)
    return {
        "device": partname,
        "tree": drawing,
        "rcc": rcc,
        "nodes": nodes,
        "dropped": dropped,
        "busIP": buses,
        "semaphores": semaphores,
        "options": options,
        "derived": _derived(options, parameters, context),
        # The values an option can take, for example, PWREXT_OverDrive_ACTIVATED
        "choices": {
            name: sorted({v["macro"] for d in parameters[name] for v in d.get("values", ())})
            for name in options
            if any(d.get("values") for d in parameters[name])
        },
        "modes": read_modes(rcc, context),
        "flash": latencies,
        "constraints": constraints,
        "clocks": attached,
        "unattached": sorted(instances - set(attached)),
    }
