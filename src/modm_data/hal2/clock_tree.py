# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
The clock trees of the STM32 with HAL2, which are not in CubeMX, from the
`Descriptors/clock/*_clock.json` files of their device family packs, in the
format of `modm_data.cubemx.clock_tree_from_partname`.

The voltage scale limits of SYSCLK and the bus of each peripheral come from the
peripheral descriptors, the peripherals that an ordering code lacks from the
limitations. The files have no register values, so the mux choices are input
names like `PLL1P_inputid`, and no oscillator modes.
"""

import re
import json
import logging

from ..kg.stmicro import did_from_string
from ..cubemx import stm32_data
from .device_data import _HAL2_PATH, _descriptor, _die_descriptor, _family, _name_pattern, _variants

LOGGER = logging.getLogger(__name__)

_KINDS = {
    "fixedSource": "fixed",
    "variableSource": "varied",
    "distinctFrequencieOscillator": "choice",
    "multiplexor": "mux",
    "divider": "div",
    "multiplier": "mul",
    "fractionalValue": "frac",
    "editableValue": "output",
}


def _clock_file(family: str, partname: str):
    """:return: The clock description of a HAL2 device, or None."""
    for path in sorted((_HAL2_PATH / f"{family}-dfp/Descriptors/clock").glob("*_clock.json")):
        if re.fullmatch(_name_pattern(path.name.removesuffix("_clock.json")), partname):
            return path
    return None


def _macro(input_id: str) -> str:
    """Some inputs are `HSE_Input` instead of `HSE_inputid`, for example, of the STM32C5 system clock mux."""
    return re.sub(r"_Input$", "_inputid", input_id)


_SOURCES = {"HSE": "HSEOSC", "LSE": "LSEOSC", "HSI": "HSIRC", "LSI": "LSIRC", "CSI": "CSIRC", "HSI48": "HSI48RC"}


def _names(elements: dict) -> dict[str, str]:
    """The core clocks get the names of the CubeMX trees."""
    names = {}
    for e in elements.values():
        if e["type"] in ("fixedSource", "variableSource") and e.get("role") in _SOURCES:
            names[e["id"]] = _SOURCES[e["role"]]
        elif e["type"] == "editableValue" and e.get("role") == "Sysclk":
            names[e["id"]] = "SysCLKOutput"
        elif e["type"] == "editableValue" and (m := re.fullmatch(r"APB(\d)_Peripheral_Clock", e["id"])):
            names[e["id"]] = f"APB{m[1]}Output"
    # HCLK clocks the flash, it is the AXI clock on the STM32H7P
    dividers = {e.get("role"): e for e in elements.values() if e["type"] == "divider"}
    if divider := dividers.get("AXICLK") or dividers.get("HCLK"):
        outputs = [elements[t] for t in divider["outputTargets"] if elements.get(t, {}).get("type") == "editableValue"]
        outputs.sort(key=lambda e: (not re.search("AHB|HCLK", e["id"]), "max" not in e))
        if outputs:
            names[outputs[0]["id"]] = "AHBOutput"
    for e in elements.values():
        if e["type"] == "editableValue" and e.get("role") == "Cpu" and e["id"] not in names:
            names[e["id"]] = "CpuClockOutput"
    return names


_PARAMS = {"SysCLKOutput": "SYSCLKFreq_VALUE", "AHBOutput": "HCLKFreq_Value", "CpuClockOutput": "CpuClockFreq_Value",
           "HSEOSC": "HSE_VALUE", "LSEOSC": "LSE_VALUE", "HSIRC": "HSI_VALUE", "LSIRC": "LSI_VALUE", "CSIRC": "CSI_VALUE",
           "HSI48RC": "HSI48_VALUE"}  # fmt: skip


# The maximum SYSCLK, HCLK and PCLK, from the RCC documentation of the CubeHAL:
# stm32c5xx_hal_rcc.h:768, stm32g5xx_hal_rcc.h:1039 and stm32h7pxx_hal_rcc.h:935.
# The `max` of the clock files is a generic value, for example, 275 MHz on every
# STM32H7P bus, while the AXI clock and HCLK may only run at up to 260 MHz.
# The STM32G5 HAL says 140 MHz PCLK, but DS14920 Table 18 and RM0514 11.5.14 allow
# the HCLK of each voltage scale, and the NUCLEO-G564RE runs at 280 MHz APB.
_CORE_LIMITS = {"STM32C5": (144e6, 144e6, 144e6), "STM32G5": (280e6, 280e6, 280e6), "STM32H7P": (520e6, 260e6, 137.5e6)}


def _core_limits(partname: str, nodes: dict):
    sysclk, hclk, pclk = next(v for p, v in _CORE_LIMITS.items() if partname.startswith(p))
    for name, node in nodes.items():
        if name in ("SysCLKOutput", "CpuClockOutput"):
            node["max"] = sysclk
        elif name == "AHBOutput" or re.fullmatch(
            r"AHB\d_Peripheral_Clock|AXI_Input_Clock|HCLK_Frequency|AHB_Clock", name
        ):
            node["max"] = hclk
        elif re.fullmatch(r"APB\dOutput", name):
            node["max"] = pclk


_SCALE = "PWR_Regulator_Voltage_Scale"


def _voltage_scales(nodes: dict, pwr: dict) -> list[tuple[str, float]]:
    """
    The SYSCLK limit of each voltage scale from the PWR descriptor, for example,
    `vos0_max` 280 MHz, `vos1_max` 240 MHz and `vos2_max` 170 MHz on the STM32G5.
    The STM32H7P lowers them further with ECC (`vos0_ecc_max`), which is not modelled.

    :return: The scale macros with their SYSCLK limit, fastest first.
    """
    features = pwr.get("features", {}) if pwr else {}
    scales = sorted((int(m[1]), float(v)) for k, v in features.items() if (m := re.fullmatch(r"vos(\d)_max", k)))
    scales = [(f"PWR_REGULATOR_VOLTAGE_SCALE{n}", limit) for n, limit in scales]
    if scales and "SysCLKOutput" in nodes:
        nodes["SysCLKOutput"]["limits"] = [{"max": limit, "when": ["eq", _SCALE, m]} for m, limit in scales]
    return scales


# The datasheets give the limit of every bus and kernel clock per voltage scale:
# DS14920 Table 18 and 19 for the STM32G5, DS15208 5.3.1 for the STM32H7P, whose
# APB2 runs at up to f_CPU_MAX / 2 and APB1/APB4 at f_CPU_MAX / 4. DS15208 Table 14
# says 275, 200 and 150 MHz for AXI, AHB and APB2, which is half of a CPU at 550,
# 400 and 300 MHz, the limits of the clock files. The flash wait states cap HCLK.
_BUS_LIMITS = {
    "STM32G5": {r"APB\dOutput": (280e6, 240e6, 170e6)},
    "STM32H7P": {"APB2Output": (260e6, 217.5e6, 175e6), r"APB[14]Output": (130e6, 108.75e6, 87.5e6)},
}
# The PLL ranges of the wide (STM32G5) and high (STM32H7P) VCO range, which
# is the one after reset, DS14920 Table 44 and DS15208 Table 41: the reference
# runs at 2 to 16 MHz, the STM32H7P VCO at 384 to 1672 MHz.
_PLL_LIMITS = {
    "STM32G5": {r"PLL\dM": {"out_min": 2e6}},
    "STM32H7P": {r"DIVM\d": {"out_min": 2e6}, r"DIVN\d": {"out_min": 384e6}},
}
# The kernel clocks the clock files get wrong, DS15208 Table 15
_KERNEL_LIMITS = {"STM32H7P": {r"ADC\d+_Input_Clock": 80e6, "UCPD_Input_Clock": 25e6}}


def _datasheet_limits(partname: str, nodes: dict, scales: list, reference: float):
    """
    The PLL ranges, and the bus and kernel clock limits per voltage scale. A kernel clock that may run
    at the CPU clock, half or a quarter of it in the clock file, which gives the
    STM32H7P limits of a 550 MHz CPU, scales with the SYSCLK limit of each scale.
    Others, like the 80 MHz of the ADC, are the same in every scale.
    """

    def gate(m):
        return ["eq", _SCALE, m]

    for family, plls in _PLL_LIMITS.items():
        for pattern, bounds in plls.items() if partname.startswith(family) else ():
            for name, node in nodes.items():
                if re.fullmatch(pattern, name):
                    node.update(bounds)
    for family, kernels in _KERNEL_LIMITS.items():
        for pattern, limit in kernels.items() if partname.startswith(family) else ():
            for name, node in nodes.items():
                if re.fullmatch(pattern, name) and node.get("limits"):
                    node["limits"][0]["max"] = limit
    if not scales:
        return
    for family, buses in _BUS_LIMITS.items():
        for pattern, limits in buses.items() if partname.startswith(family) else ():
            for name, node in nodes.items():
                if re.fullmatch(pattern, name):
                    node["max"] = limits[0]
                    node["limits"] = [{"max": limit, "when": gate(m)} for (m, _), limit in zip(scales, limits)]
    for node in nodes.values():
        if node["kind"] != "output" or "enable" not in node or not node.get("limits"):
            continue
        limit = node["limits"][0]
        if (ratio := limit.get("max", 0) / reference) in (1, 0.5, 0.25):
            node["limits"] = [{"max": s * ratio, "when": ["and", limit["when"], gate(m)]} for m, s in scales]


def _flash_latency(partname: str, scales: list, nodes: dict) -> list[dict]:
    """
    The wait states from the tables of `stm32_data`, keyed by the core voltage of
    each scale, see `cubemx.clock_scales.manual_flash_latency`. Their last entry
    caps HCLK per scale, which is the AXI clock on the STM32H7P, while the PWR
    descriptor caps SYSCLK.
    """
    table = stm32_data.getFlashLatencyForDevice(did_from_string(partname.lower()))
    if not table:
        return []
    voltages = sorted(table, reverse=True)
    gates = [["eq", _SCALE, m] for m, _ in scales] if len(scales) == len(voltages) else [None]
    if gates[0] and "AHBOutput" in nodes:
        nodes["AHBOutput"]["limits"] = [{"max": float(table[mv][-1]), "when": g} for g, mv in zip(gates, voltages)]
    latencies = []
    for gate, mv in zip(gates, voltages):
        for states, limit in enumerate(table[mv]):
            when = ["cmp", "HCLKFreq_Value", "<=", str(limit)]
            latencies.append({"latency": f"FLASH_LATENCY_{states}", "when": ["and", gate, when] if gate else when})
    return latencies


# RM0522 9.4.3 Table 66: the STM32C5 PSI generates 100, 144 or 160 MHz from a
# reference of 32.768 kHz (LSE), 8 MHz (HSI/18) or one of these HSE frequencies.
# From the LSE it is an integer multiple of 32.768 kHz, 3052, 4395 or 4883 times,
# see the PSI characteristics in the clock file.
_PSI = {100e6: (8e6, 16e6, 24e6, 25e6, 32e6, 48e6, 50e6), 144e6: (8e6, 16e6, 24e6, 32e6, 48e6),
        160e6: (8e6, 16e6, 24e6, 32e6, 48e6)}  # fmt: skip


def _psi(node: dict):
    """The PSI as a multiplier of its reference, each factor for one reference frequency."""
    domain = []
    for target, references in _PSI.items():
        for reference in (*references, 32768.0):
            frequency = round(target / reference) * reference if reference < 1e6 else target
            macro = f"PSI_{target / 1e6:g}MHZ_REF_{reference / 1e6:g}MHZ"
            domain.append({"factor": frequency / reference, "macro": macro, "input": reference})
    node.update(kind="mul", domain=domain)
    node["default"] = next(d["factor"] for d in domain if d["macro"] == "PSI_100MHZ_REF_8MHZ")
    node.pop("range", None)


# The defaults of the clock files are those of the CubeMX2 user interface, the
# modm code generator needs the reset values. "NoClock" is no input of the mux,
# so a PLL without a source has no frequency and no limits to check.
_RESET = {
    # RM0514 11.8.6 RCC_PLL1CFGR, 11.8.8 RCC_PLL1DIVR and 11.8.42 RCC_CCIPR5
    "STM32G5": {
        r"PLL\d_Clock_Source": "NoClock",
        r"PLL\dN": 129,
        r"PLL\d[PQR]": 2,
        "FDCAN_Clock_Source": "HSE_inputid",
    },
    # RM0533 7.8.12-7.8.17 RCC_PLLxDIVR1/2 and 7.8.25 RCC_CCIPR2
    "STM32H7P": {r"DIV[PQRS]\d": 2, "FDCAN_Clock_Source": "HSE_inputid"},
}


def _reset_values(partname: str, nodes: dict):
    for family, values in _RESET.items():
        if not partname.startswith(family):
            continue
        for pattern, value in values.items():
            for name, node in nodes.items():
                if re.fullmatch(pattern, name):
                    node["default"] = value if isinstance(value, str) else float(value)
    # RM0514 11.8.8: PLL1P allows only even factors, PLL1R every one, the clock file has it the other way around
    if partname.startswith("STM32G5"):
        for name in ("PLL1P", "PLL1R"):
            for key in ("range", "domain", "min", "max"):
                nodes[name].pop(key, None)
        nodes["PLL1P"]["domain"] = [{"factor": float(f), "macro": f"{f}"} for f in range(2, 129, 2)]
        nodes["PLL1R"].update(range=[1, 128], min=1.0, max=128.0)


def _bus_peripherals(rcc: dict, nodes: dict) -> dict[str, str]:
    """
    The peripherals of each bus from the RCC descriptor, for example, `"APB1": ["I2C1", "TIM2"]`,
    with groups like ADC12 for ADC1 and ADC2.

    :return: The bus output of each peripheral.
    """
    buses = {}
    for group in (rcc or {}).get("features", {}).values():
        if not isinstance(group, dict):
            continue
        for bus, names in group.items():
            output = next((n for n in (f"{bus}Output", f"{bus}_Peripheral_Clock") if n in nodes), None)
            if output is None and bus.startswith("AHB"):
                output = "AHBOutput"
            for name in names:
                m = re.fullmatch(r"(.*[A-Za-z])(\d{2,})", name)
                for instance in [m[1] + d for d in m[2]] if m and name not in nodes else [name]:
                    buses[instance] = output
    return buses


def clock_tree_from_partname(partname: str) -> dict | None:
    """
    :param partname: A full STM32 device name of a HAL2 family, for example, STM32G553CETx.
    :return: The clock tree like `modm_data.cubemx.clock_tree_from_partname`, or None.
    """
    family = _family(partname)
    if not family or not (path := _clock_file(family, partname)):
        LOGGER.error(f"No clock tree found for {partname}")
        return None
    die = _descriptor(family, "pinout", partname)["characteristics"]["die_name"]
    descriptors = {p.get("name"): p for p in _die_descriptor(family, die, "peripheral")["peripherals"]}
    # The peripherals that every ordering code of the device lacks, for example, AES and FDCAN1 on the STM32C551
    limitations = _die_descriptor(family, die, "limitation")["limitations"]
    removed = set.intersection(
        *(set(limitations.get(v.get("Dvariant"), {}).get("removed_peripherals", [])) for v in _variants()[partname])
    )
    tree = json.loads(path.read_text())["tree"]
    elements = {e["id"]: e for e in tree["elements"] if e["type"] in _KINDS}
    # Some links are only drawn, for example, the reference clock mux of the STM32C5 PSI
    for transition in tree["transitions"]:
        source = transition["sourceTaskId"].removesuffix("_output")
        target = transition["targetTaskId"].removesuffix("_input").split("__")[0]
        if source in elements and target in elements and target not in elements[source]["outputTargets"]:
            elements[source]["outputTargets"].append(target)
    names = _names(elements)

    def name(i):
        return names.get(i, i)

    nodes = {}
    for e in elements.values():
        node = {"kind": _KINDS[e["type"]], "inputs": [], "outputs": [], "signals": []}
        node["param"] = _PARAMS.get(name(e["id"]), e["id"])
        if e["type"] == "multiplexor":
            node["inputs"] = [
                {"from": name(i["from"]), "signal": i.get("label", ""), "macro": _macro(i["input_Id"])}
                for i in e.get("possible_Input", ())
                if i.get("from") in elements
            ]
            node["default"] = _macro(e["default"])
        elif e["type"] in ("fixedSource", "variableSource"):
            node["value" if e["type"] == "fixedSource" else "default"] = float(e["value"])
        elif e["type"] == "distinctFrequencieOscillator":
            node["domain"] = [
                {"value": float(o["const"]), "macro": str(o.get("titre", o["const"]))} for o in e["oneOf"]
            ]
            node["default"] = float(e["default"])
            if e["id"] == "PSI":
                _psi(node)
        elif e["type"] in ("divider", "multiplier"):
            factors = sorted(float(o["const"]) for o in e.get("oneOf", ()))
            if factors and factors == [float(f) for f in range(int(factors[0]), int(factors[-1]) + 1)]:
                node["range"] = [int(factors[0]), int(factors[-1])]
                node["min"], node["max"] = factors[0], factors[-1]
            else:
                node["domain"] = [{"factor": f, "macro": f"{f:g}"} for f in factors]
            node["default"] = float(e["default"])
        elif e["type"] == "fractionalValue":
            node["range"] = [e.get("min", 0), e.get("max", 8191)]
            node["default"] = float(e.get("default") or 0)
        # The limits of a divider or multiplier are those of its output frequency
        prefix = "out_" if e["type"] in ("divider", "multiplier") else ""
        for bound in ("min", "max"):
            # A minimum of 0 says nothing
            if e.get(bound):
                node[prefix + bound] = float(e[bound])
        if e["type"] == "editableValue" and e.get("clocked_instance") and name(e["id"]) == e["id"]:
            # A kernel clock is in use only for its peripherals, and its limits apply only then,
            # like those of the CubeMX trees, which test the usage flags
            flags = [f"{i}Enable" for i in e["clocked_instance"]]
            node["enable"] = ",".join(flags)
            bounds = {bound: node.pop(bound) for bound in ("min", "max") if bound in node}
            if bounds:
                node["limits"] = [{**bounds, "when": ["or", *(["var", f] for f in flags)]}]
        nodes[name(e["id"])] = node
    _reset_values(partname, nodes)

    for e in elements.values():
        for target in e["outputTargets"]:
            if target not in elements:
                continue
            # The input divider of a PLL feeds the multiplier through its fractional part,
            # which is a setting of the multiplier, not a clock
            if elements[target]["type"] == "fractionalValue":
                for multiplier in elements[target]["outputTargets"]:
                    nodes[name(e["id"])]["outputs"].append(name(multiplier))
                    nodes[name(multiplier)]["inputs"].append({"from": name(e["id"]), "signal": ""})
                continue
            nodes[name(e["id"])]["outputs"].append(name(target))
            if elements[target]["type"] != "multiplexor":
                nodes[name(target)]["inputs"].append({"from": name(e["id"]), "signal": ""})
    # The fractional part of a PLL multiplier
    for node in nodes.values():
        if node["kind"] == "mul" and any(nodes[i["from"]]["kind"] == "frac" for i in node["inputs"]):
            node["kind"] = "mulfrac"
            node["inputs"].sort(key=lambda i: nodes[i["from"]]["kind"] == "frac")

    # The peripherals at the output that clocks the fewest of them, TIM2 has its own on the STM32H7P
    clocks = {}
    outputs = sorted(
        (e for e in elements.values() if e["type"] == "editableValue" and e.get("clocked_instance")),
        key=lambda e: -len(e["clocked_instance"]),
    )
    for e in outputs:
        for instance in e["clocked_instance"]:
            clocks[instance] = name(e["id"])
    buses = _bus_peripherals(descriptors.get("RCC"), nodes)
    for instance, bus in buses.items():
        clocks.setdefault(instance, bus)
    for instance in removed:
        clocks.pop(instance, None)
    for instance, output in clocks.items():
        if instance in nodes or output not in nodes:
            continue
        nodes[instance] = {"kind": "peripheral", "inputs": [{"from": output, "signal": ""}], "outputs": [],
                           "signals": [], "via": output}  # fmt: skip
        # A peripheral with a kernel clock needs its bus clock too
        if (bus := buses.get(instance)) and bus != output and bus in nodes:
            nodes[instance]["bus"] = bus
        nodes[output]["outputs"].append(instance)

    # The fastest clock of the clock file, which its kernel clock limits are relative to
    reference = max(nodes.get(n, {}).get("max", 0) for n in ("SysCLKOutput", "CpuClockOutput"))
    _core_limits(partname, nodes)
    scales = _voltage_scales(nodes, descriptors.get("PWR"))
    flash = _flash_latency(partname, scales, nodes)
    _datasheet_limits(partname, nodes, scales, reference)
    options, derived = {}, {}
    if scales:
        options[_SCALE] = scales[0][0]
        # The slowest scale that SYSCLK fits, like the derived voltage scales of CubeMX
        derived[_SCALE] = [{"value": m, "when": ["cmp", "SYSCLKFreq_VALUE", "<=", f"{limit:.0f}"]} for m, limit in
                           reversed(scales)] + [{"value": scales[0][0]}]  # fmt: skip
    return {
        "device": partname,
        "tree": path.name.removesuffix("_clock.json"),
        "rcc": None,
        "nodes": nodes,
        "dropped": 0,
        "busIP": {},
        "semaphores": {},
        "options": options,
        "derived": derived,
        "choices": {_SCALE: [m for m, _ in scales]} if scales else {},
        "modes": {},
        "flash": flash,
        "constraints": [],
        "clocks": clocks,
        "unattached": [],
    }
