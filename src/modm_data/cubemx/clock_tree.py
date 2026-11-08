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
from collections import defaultdict
from functools import cache

from ..utils import XmlReader
from . import conditions
from .device_data import _MCU_PATH, _device_file, _is_software_ip, header_from_partname

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


# ================================ PARAMETERS =================================
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
                }
                for value in values
            ]
        declaration["when"] = [c.get("Expression") for c in element.findall("Condition") if c.get("Expression")]
        parameters[name].append(declaration)
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


def _number(label: str) -> float:
    """:return: The numeric factor a possible value carries, for example, `4` or `/2`."""
    match = re.search(r"-?\d+(?:\.\d+)?", label.replace(",", "."))
    return float(match.group(0)) if match else None


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
    static = {}
    for key, pick in (("min", min), ("max", max)):
        if values := [v[key] for v in variants if key in v]:
            static[key] = pick(values)
    if len({(v.get("min"), v.get("max")) for v in variants}) < 2:
        return static, []
    return static, variants


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

    # An element may name an edge on one side only, and the drawing needs both
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


# ================================ PERIPHERALS ================================
# Listed as IPs but not clocked peripherals: the clock controller itself, the
# system and pin configuration entries and the generic DMA entry, whose real
# instances are listed separately as DMA1 and DMA2
_NOT_CLOCKED = {"RCC", "SYS", "GPIO", "NVIC", "DEBUG", "MEMORYMAP", "BOOTPATH", "PWR"}

# The tree and the MCU description do not always spell a peripheral the same
_ALIASES = {
    "HDMI_CEC": "CEC",
    "QUADSPI": "QSPI",
    "OCTOSPI1": "OCTOSPI",
    "USB_DRD_FS": "USB",
    "USB_OTG_FS": "USB_OTG",
    "USB_OTG_HS": "USB_OTG",
    "SUBGHZ": "SUBGHZSPI",
}

# RCC spells its enable registers several ways: AHBENR, AHB1ENR, APBENR1,
# APB1ENR and, on the STM32H7, APB1LENR and APB1HENR for the two halves of one
# bus. The index may sit before or after ENR, and IOP is the GPIO part of AHB.
_ENABLE_MACRO = re.compile(r"^RCC_(?P<bus>IOP|AHB|APB)(?P<index>\d*)[LH]?ENR(?P<suffix>\d*)_(?P<name>\w+)EN_Pos$")


def peripherals_of(device_file: XmlReader) -> set[str]:
    """:return: Every hardware peripheral instance the MCU description lists."""
    instances = set()
    for ip in device_file.query("//IP"):
        name = ip.get("InstanceName")
        if not name or name.upper() in _NOT_CLOCKED or name.upper().startswith("CORTEX_M"):
            continue
        if _is_software_ip(ip.get("Name"), ip.get("Version")):
            continue
        instances.add(name)
    return instances


def header_buses(header) -> dict[str, list[str]]:
    """
    The RCC clock enable registers name every peripheral on a bus, which is
    what the clock tree leaves out for the peripherals it does not mux.

    :param header: The CMSIS device header, which is more correct than the SVD.
    :return: The peripheral instances per bus.
    """
    buses = defaultdict(set)
    for macro in header.macros:
        if match := _ENABLE_MACRO.fullmatch(macro):
            # The GPIO ports sit on AHB, and the two halves of an STM32H7 APB are one bus
            prefix = "AHB" if match["bus"] == "IOP" else match["bus"]
            buses[prefix + (match["index"] or match["suffix"])].add(match["name"])
    return {bus: sorted(names) for bus, names in sorted(buses.items())}


def _spellings(name: str) -> tuple[list[str], list[str]]:
    """
    The clock tree muxes a group (`ADCoutput`) where the device lists instances
    (`ADC1`, `ADC2`), so a peripheral is looked up under several spellings.

    :return: The exact spellings of a peripheral and the loose ones that drop
             the instance number. A loose spelling is a last resort: `TIM2`
             belongs to the bus that lists it, not to `TIM1output` just because
             both start with `TIM`.
    """
    upper = name.upper()
    exact = [upper] + ([alias] if (alias := _ALIASES.get(upper)) and alias != upper else [])
    loose = [s for s in (re.sub(r"\d+$", "", e) for e in exact) if s and s not in exact]
    return exact, list(dict.fromkeys(loose))


def attach_peripherals(nodes: dict, buses: dict, header_buses: dict, instances: set) -> dict[str, str]:
    """
    Adds one leaf node per peripheral instance, wired to whatever clocks it.

    The tree itself only ends in bus outputs and in the few peripherals that
    have a clock source of their own, so the peripherals are attached here: to
    their own output node when the tree muxes them, to the bus output that
    lists them, and otherwise to the bus its clock enable bit sits in.

    :param buses: The peripherals per bus output node, see `bus_peripherals`.
    :param header_buses: The peripherals per bus, see `header_buses`.
    :param instances: The peripherals the device has, see `peripherals_of`.
    :return: The node that clocks each peripheral.
    """
    # Which tree node stands for a bus of the header, for example, APB1 -> APBOutput
    outputs = [name for name, node in nodes.items() if node["kind"] == "output"]
    bus_node = {}
    for bus in header_buses:
        found = [name for name in outputs if name.upper().startswith(bus.upper())]
        if not found:
            # A device with one APB answers to APB1 and APB2 alike
            stem = re.sub(r"\d+$", "", bus).upper()
            found = [name for name in outputs if name.upper().startswith(stem)]
        if found:
            bus_node[bus] = sorted(found, key=len)[0]

    muxed = {}
    for name in outputs:
        if name.lower().endswith("output"):
            for spelling in sum(_spellings(name[: -len("output")].rstrip("_")), []):
                muxed.setdefault(spelling, name)

    def find(names: list[str]) -> str:
        """The node that clocks a peripheral under any of these spellings."""
        for spelling in names:
            if spelling in muxed:
                return muxed[spelling]
        for spelling in names:
            for node, group in buses.items():
                if spelling in {g.upper() for g in group}:
                    return node
        for spelling in names:
            for bus, group in header_buses.items():
                if bus in bus_node and spelling in {g.upper() for g in group}:
                    return bus_node[bus]
        return None

    attached = {}
    for peripheral in sorted(instances):
        exact, loose = _spellings(peripheral)
        source = find(exact) or find(loose)
        if source is None or source not in nodes:
            continue
        nodes[peripheral] = {
            "kind": "peripheral",
            "inputs": [{"from": source, "signal": ""}],
            "outputs": [],
            "signals": [],
            "via": source,
        }
        nodes[source]["outputs"] = sorted({*nodes[source]["outputs"], peripheral})
        attached[peripheral] = source
    return attached


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


def _options(nodes: dict, parameters: dict, semaphores: dict) -> dict[str, str]:
    """:return: The parameters that a derivation rule tests but no node stands for."""
    wanted, bound = set(), {node["param"] for node in nodes.values() if "param" in node}
    stack = [
        rule.get("when")
        for node in nodes.values()
        for key in ("rules", "limits", "out_limits")
        for rule in node.get(key, ())
    ]
    while stack:
        gate = stack.pop()
        if not isinstance(gate, list):
            continue
        if gate[0] == "eq":
            wanted.add(gate[1])
        elif gate[0] == "var" and gate[1] in semaphores:
            wanted.add(semaphores[gate[1]]["param"])
        stack += gate[1:]
    return {name: parameters[name][0]["default"] for name in sorted(wanted - bound) if name in parameters}


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
    attached = attach_peripherals(nodes, buses, header_buses(header) if header else {}, instances)

    semaphores = _semaphores(parameters)
    return {
        "device": partname,
        "tree": drawing,
        "rcc": rcc,
        "nodes": nodes,
        "dropped": dropped,
        "busIP": buses,
        "semaphores": semaphores,
        "options": _options(nodes, parameters, semaphores),
        "clocks": attached,
        "unattached": sorted(instances - set(attached)),
    }


# ================================= EVALUATION =================================
def _test(gate: list, settings: dict, semaphores: dict) -> bool:
    """
    Tests a rule condition against the parameter settings. An identifier that
    neither a setting nor a semaphore answers is false: `USBUsed_ForRCC` holds
    only when the configuration says that USB is in use.
    """
    kind = gate[0]
    if kind == "const":
        return bool(gate[1])
    if kind == "eq":
        return settings.get(gate[1]) == gate[2]
    if kind == "cmp":
        try:
            left, right = float(settings[gate[1]]), float(gate[3])
        except (KeyError, ValueError):
            return False
        return {"<": left < right, ">": left > right, "<=": left <= right, ">=": left >= right}.get(gate[2], False)
    if kind == "var":
        if (flag := semaphores.get(gate[1])) and flag["param"] in settings:
            return settings[flag["param"]] == flag["macro"]
        return settings.get(gate[1], "").lower() in ("1", "true")
    values = [_test(g, settings, semaphores) for g in gate[1:]]
    if kind == "not":
        return not values[0]
    return all(values) if kind == "and" else any(values)


def identifiers(tree: dict) -> set[str]:
    """:return: Every identifier that the rules and limits of the tree test."""
    names, stack = (
        set(),
        [
            rule.get("when")
            for node in tree["nodes"].values()
            for key in ("rules", "limits", "out_limits")
            for rule in node.get(key, ())
        ],
    )
    while stack:
        gate = stack.pop()
        if isinstance(gate, list) and gate:
            if gate[0] in ("var", "eq", "cmp"):
                names.add(gate[1])
            else:
                stack += gate[1:]
    return names


def _frequency(text: str) -> float:
    """:return: A frequency like `8000000`, `8M`, `8 MHz` or `32.768k` in Hz."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*", text)
    if not match:
        raise ValueError(f"not a frequency: '{text}'")
    return float(match[1]) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[match[2].lower()]


def _setting(name: str, node: dict, value: str):
    """
    :param value: A macro, the name of an input node or its signal for a mux,
                  a factor for a divider or multiplier, a frequency for a source.
    :return: The macro of the chosen mux input, or the factor or frequency.
    """
    value = str(value).strip()
    lower = value.lower()
    if node["kind"] == "mux":
        for input in node["inputs"]:
            macro = input.get("macro", "")
            names = (macro.lower(), input["from"].lower(), input["signal"].lower())
            if lower in names or (macro and macro.lower().endswith("_" + lower)):
                return macro or input["from"]
        choices = ", ".join(i.get("macro") or i["from"] for i in node["inputs"])
        raise ValueError(f"{name} has no input '{value}', choose from {choices}")
    if node["kind"] == "choice":
        for option in node.get("domain", ()):
            if lower == option["macro"].lower():
                return option["value"]
        frequency = _frequency(value)
        if node.get("domain") and frequency not in {o["value"] for o in node["domain"]}:
            raise ValueError(
                f"{name} cannot be {value}, choose from " + ", ".join(f"{o['value']:g}" for o in node["domain"])
            )
        return frequency
    if node["kind"] in ("fixed", "varied"):
        return _frequency(value)
    for option in node.get("domain", ()):
        if lower == option["macro"].lower() or _number(value) == option["factor"]:
            return option["factor"]
    if "range" in node and (number := _number(value)) is not None:
        low, high = node["range"]
        if not low <= number <= high:
            raise ValueError(f"{name} must be within {low}..{high}, not {value}")
        return number
    choices = ", ".join(f"{o['factor']:g}" for o in node.get("domain", ())) or "nothing, it is fixed"
    raise ValueError(f"{name} cannot be '{value}', choose from {choices}")


def evaluate(tree: dict, config: dict[str, str] | None = None) -> dict[str, dict]:
    """
    The frequency of every node for one configuration, with everything that the
    configuration does not set at its default.

    :param tree: The clock tree, see `clock_tree_from_partname`.
    :param config: The settings by node or parameter name, see `_setting`, for
                   example, `{"PLLN": "20", "SysClkSource": "PLLR"}`. Options
                   that no node stands for take the macro as given, for
                   example, `{"PWR_Regulator_Voltage_Scale": "SCALE2"}`, and so
                   do the flags that rules test, for example, `{"USBUsed_ForRCC": "1"}`.
    :return: The `freq`uency in Hz of every node, None if it has none, its
             `setting`, whether it is `used` by an output through the chosen
             mux inputs, and the limits it violates as `errors`.
    """
    nodes, semaphores = tree["nodes"], tree["semaphores"]
    by_param = {node["param"]: name for name, node in nodes.items() if "param" in node}
    chosen, settings = {}, dict(tree["options"])
    for key, value in (config or {}).items():
        if key in nodes:
            chosen[key] = _setting(key, nodes[key], value)
        elif key in by_param:
            chosen[by_param[key]] = _setting(by_param[key], nodes[by_param[key]], value)
        elif key in tree["options"] or key in identifiers(tree):
            # An option or a flag like USB_OTG_FSUsed_ForRCC=1 that a rule tests,
            # options also by the end of their macro, for example, SCALE2
            value = str(value).strip()
            macros = [f["macro"] for f in semaphores.values() if f["param"] == key]
            settings[key] = next(
                (m for m in macros if m.lower() == value.lower() or m.lower().endswith("_" + value.lower())), value
            )
        else:
            raise ValueError(f"unknown node or parameter '{key}'")

    def setting(name: str, node: dict):
        if name in chosen:
            return chosen[name]
        return node.get("value", node.get("default"))

    # The macro every parameter stands at, which the derivation rules test
    for name, node in nodes.items():
        if "param" not in node or (value := setting(name, node)) is None:
            continue
        if node["kind"] == "mux":
            settings[node["param"]] = value
        else:
            found = [d["macro"] for d in node.get("domain", ()) if d.get("factor", d.get("value")) == value]
            settings[node["param"]] = found[0] if found else f"{value:g}"

    def factor(name: str, node: dict) -> float:
        for rule in node.get("rules", ()):
            if "when" in rule and _test(rule["when"], settings, semaphores):
                return rule["factor"]
        defaults = [rule["factor"] for rule in node.get("rules", ()) if "when" not in rule]
        return defaults[0] if defaults and name not in chosen else setting(name, node)

    result = {}

    def freq_of(name: str) -> float:
        if name in result:
            return result[name]["freq"]
        node = nodes[name]
        result[name] = {"freq": None}  # breaks cycles
        kind, sources = node["kind"], [i["from"] for i in node["inputs"] if i["from"] in nodes]
        value, freq = setting(name, node), None
        if kind in ("fixed", "varied", "choice"):
            freq = value
        elif kind == "mux":
            picked = [i["from"] for i in node["inputs"] if value in (i.get("macro"), i["from"])]
            freq = freq_of(picked[0]) if picked and picked[0] in nodes else None
        elif kind == "mulfrac":
            inputs = [s for s in sources if nodes[s]["kind"] != "frac"]
            fraction = sum(setting(s, nodes[s]) or 0 for s in sources if nodes[s]["kind"] == "frac")
            if inputs and (f := freq_of(inputs[0])) is not None and value is not None:
                freq = f * (value + fraction / 8192)
        elif kind in ("div", "mul"):
            if sources and (f := freq_of(sources[0])) is not None and (k := factor(name, node)):
                freq = f / k if kind == "div" else f * k
        elif kind in ("output", "peripheral") and sources:
            freq = freq_of(sources[0])
        result[name] = {"freq": freq, "setting": value, "used": False, "errors": []}
        return freq

    for name in nodes:
        freq_of(name)

    # What an output or peripheral draws from through the chosen mux inputs
    stack = [name for name, node in nodes.items() if node["kind"] in ("output", "peripheral")]
    while stack:
        name = stack.pop()
        if result[name]["used"]:
            continue
        result[name]["used"] = True
        node = nodes[name]
        for input in node["inputs"]:
            if input["from"] in nodes and (
                node["kind"] != "mux" or result[name]["setting"] in (input.get("macro"), input["from"])
            ):
                stack.append(input["from"])

    def check(name: str, freq: float, static: dict, variants: list, prefix: str):
        bounds = static
        for variant in variants:
            if "when" not in variant or _test(variant["when"], settings, semaphores):
                bounds = variant
                break
        low, high = bounds.get(f"{prefix}min"), bounds.get(f"{prefix}max")
        if low is not None and freq < low * (1 - 1e-9):
            result[name]["errors"].append(f"{freq:g} < {low:g}")
        if high is not None and freq > high * (1 + 1e-9):
            result[name]["errors"].append(f"{freq:g} > {high:g}")

    for name, node in nodes.items():
        if (freq := result[name]["freq"]) is None or not result[name]["used"]:
            continue
        if node["kind"] in ("fixed", "varied", "output"):
            check(name, freq, node, node.get("limits", ()), "")
        check(name, freq, node, node.get("out_limits", ()), "out_")
    return result
