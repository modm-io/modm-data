# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Clock Tree Peripherals

Attaches the peripherals of a device to their kernel and bus clocks, from
the RCC enable bits and bus base addresses of the CMSIS header.
"""

import re
from collections import defaultdict

from ..utils import XmlReader
from .device_data import _is_software_ip


# Listed as IPs but not clocked peripherals: the clock controller itself, the
# system and pin configuration entries and the parts of the Cortex core
_NOT_CLOCKED = {
    "RCC",
    "SYS",
    "SYS_S",
    "GPIO",
    "NVIC",
    "DEBUG",
    "MEMORYMAP",
    "BOOTPATH",
    "PWR",
    "SAU",
    "GIC",
    "SYS_M4",
    "BOOT",
}

# The tree, the MCU description and the RCC enable bits of the header do not
# always spell a peripheral the same, for example, the STM32H7 enables DAC1
# with DAC12EN, ETH with ETH1MACEN and USB_OTG_FS with USB2OTGFSEN
_ALIASES = {
    "HDMI_CEC": ("CEC",),
    "QUADSPI": ("QSPI",),
    "QUADSPI1": ("QUADSPI", "QSPI"),
    "GTZC": ("GTZC1",),
    # The STM32L4+ clocks both OCTOSPI with the OCTOSPIM mux, the STM32H7A3 enables the manager as IOMNGR
    "OCTOSPI1": ("OCTOSPI", "OSPI1", "OCTOSPIM"),
    "OCTOSPI2": ("OCTOSPI", "OSPI2", "OCTOSPIM"),
    "OCTOSPIM": ("OSPIM", "IOMNGR"),
    "DSIHOST": ("DSI",),
    # The STM32F413, F427 and F469 clock the two blocks of their only SAI apart, block A stands for it
    "SAI1": ("SAI1A", "SAIA", "SAI_A", "SAI_ACLK"),
    # The 48 MHz clock of the STM32L4 and STM32WB, after the own mux of a device
    "USB": ("CK48", "USB1"),
    "USB_DRD_FS": ("USB", "CK48"),
    "USB_OTG_FS": ("USBOTGFS", "USBOFS", "USBFS", "USB_OTG", "USB", "OTGFS", "USB2OTGFS", "OTG", "CK48"),
    "USB_OTG_HS": ("USBOTGHS", "USBHS", "USB_OTG", "USB", "OTGHS", "USB1OTGHS"),
    "SUBGHZ": ("SUBGHZSPI",),
    # The MCU description lists one DMA where the header enables DMA1
    "DMA": ("DMA1",),
    "DAC1": ("DAC12",),
    "ETH": ("ETH1MAC", "ETHMAC"),
    "JPEG": ("JPGDEC",),
    "COMP1": ("COMP12", "COMP"),
    "COMP2": ("COMP12", "COMP"),
    "VREFBUF": ("VREF", "SYSCFG"),
    # Generated from the TIM16 and TIM17 outputs, with their clock
    "IRTIM": ("TIM16",),
    # The tamper registers share the clock enable of the RTC registers
    "TAMP": ("RTCAPB",),
    # The STM32U5 enables both camera interfaces with one bit, and the FMC as FSMC
    "DCMI": ("DCMI_PSSI",),
    "PSSI": ("DCMI_PSSI",),
    "FMC": ("FSMC",),
}

# Families without an own enable bit clock the comparators and operational
# amplifiers with the system configuration controller, so SYSCFG is a fallback
_PATTERNS = (
    (re.compile(r"COMP\d+"), ("COMP12", "COMP", "SYSCFG")),
    (re.compile(r"OPAMP\d+"), ("OPAMP", "SYSCFG")),
)

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


_BASE_BUS = re.compile(r"\b(?:(?P<domain>D\d)_)?(?P<bus>AHB|APB)(?P<index>\d*)[LH]?PERIPH_BASE")
# The domains of the STM32H7 number their buses differently than RCC
_DOMAINS = {("D1", "AHB1"): "AHB3", ("D1", "APB1"): "APB3", ("D3", "AHB1"): "AHB4", ("D3", "APB1"): "APB4"}


def base_buses(header) -> dict[str, str]:
    """
    The bus of every peripheral by its base address, which is how the header
    derives it, for example, `ICACHE_BASE_NS (AHB1PERIPH_BASE_NS + 0x10400UL)`.
    This is the last resort for blocks without a clock enable bit.

    :return: The bus of each `<NAME>_BASE` macro, by NAME.
    """
    buses = {}
    for macro, value in header.macros.items():
        if not (match := re.fullmatch(r"(\w+?)_BASE(?:_NS)?", macro)):
            continue
        for _ in range(4):
            if found := _BASE_BUS.search(value):
                bus = found["bus"] + found["index"]
                buses.setdefault(match[1], _DOMAINS.get((found["domain"], bus), bus))
                break
            # follow an alias of another base address
            if not (alias := re.search(r"\b(\w+_BASE(?:_NS)?)\b", value)) or alias[1] not in header.macros:
                break
            value = header.macros[alias[1]]
    return buses


def _spellings(name: str, shared: bool = True) -> tuple[list[str], list[str]]:
    """
    The clock tree muxes a group (`ADCoutput`) where the device lists instances
    (`ADC1`, `ADC2`), so a peripheral is looked up under several spellings.

    :return: The exact spellings of a peripheral and the loose ones that drop
             the instance number. A loose spelling is a last resort: `TIM2`
             belongs to the bus that lists it, not to `TIM1output` just because
             both start with `TIM`.
    """
    upper = name.upper()
    # The STM32H7 muxes UART4 with USART234578, and I2S1 with SPI123
    _extra = [re.sub(r"^UART", "USART", upper)] if shared and re.fullmatch(r"UART\d+", upper) else []
    _extra += [re.sub(r"^I2S", "SPI", upper)] if shared and re.fullmatch(r"I2S\d+", upper) else []
    # TrustZone devices list GTZC_NS and GTZC_S, the header enables GTZC1
    names = [upper, *([stem, stem + "1"] if (stem := re.sub(r"_N?S$", "", upper)) != upper else [])]
    extra = [a for name in names for a in _ALIASES.get(name, ())]
    extra += [a for pattern, aliases in _PATTERNS if pattern.fullmatch(upper) for a in aliases]
    exact = list(dict.fromkeys([*names, *extra, *_extra]))
    # Only the instance number of the peripheral itself is dropped, CK48 is not CK
    number = re.search(r"\d+$", upper)
    loose = [e[: -len(number[0])] for e in exact if e.endswith(number[0])] if number else []
    loose = [s for s in loose if s and s not in exact]
    return exact, list(dict.fromkeys(loose))


def attach_peripherals(
    nodes: dict, buses: dict, header_buses: dict, instances: set, base_buses: dict | None = None
) -> dict[str, str]:
    """
    Adds one leaf node per peripheral instance, wired to whatever clocks it.

    The tree itself only ends in bus outputs and in the few peripherals that
    have a clock source of their own, so the peripherals are attached here: to
    their own output node when the tree muxes them, to the bus output that
    lists them, and otherwise to the bus its clock enable bit sits in.

    :param buses: The peripherals per bus output node, see `bus_peripherals`.
    :param header_buses: The peripherals per bus, see `header_buses`.
    :param instances: The peripherals the device has, see `peripherals_of`.
    :param base_buses: The bus of each peripheral by its base address, see `base_buses`.
    :return: The node that clocks each peripheral.
    """
    # Which tree node stands for a bus of the header, for example, APB1 -> APBOutput
    outputs = [name for name, node in nodes.items() if node["kind"] == "output"]
    base_buses = base_buses or {}
    bus_node = {}
    for bus in {*header_buses, *base_buses.values()}:
        found = [name for name in outputs if name.upper().startswith(bus.upper())]
        if not found:
            # A device with one APB answers to APB1 and APB2 alike
            stem = re.sub(r"\d+$", "", bus).upper()
            found = [name for name in outputs if name.upper().startswith(stem)]
        if found:
            bus_node[bus] = sorted(found, key=len)[0]
    # The STM32WB0 and STM32WL3 draw no bus outputs, their buses run from the system clock
    core = [
        name
        for clock in ("hclkfreq_value", "sysclkfreq_value")
        for name in outputs
        if nodes[name].get("param", "").lower() == clock
        and not any(nodes.get(i["from"], {}).get("kind") == "output" for i in nodes[name]["inputs"])
    ]
    if core:
        for bus in {*header_buses, *base_buses.values()} - set(bus_node):
            bus_node[bus] = core[0]

    def timer_output(bus: str) -> str:
        """The output of the x1/x2 timer multiplier behind the prescaler of a bus output."""
        prescalers = [i["from"] for i in nodes[bus]["inputs"] if i["from"] in nodes]
        found = {
            output
            for prescaler in prescalers
            for mul in nodes[prescaler]["outputs"]
            if nodes.get(mul, {}).get("kind") == "mul"
            for output in nodes[mul]["outputs"]
            if nodes.get(output, {}).get("kind") == "output"
        }
        return found.pop() if len(found) == 1 else None

    # The STM32H7 calls its timer clocks Tim1Output and Tim2Output, which are
    # the outputs of a multiplier and not the own clock of TIM1 and TIM2
    timers = {
        name for name in outputs if any(nodes.get(i["from"], {}).get("kind") == "mul" for i in nodes[name]["inputs"])
    }
    muxed = {}
    stems = [(name, name[: -len("output")].rstrip("_")) for name in outputs]
    stems = [(name, stem) for name, stem in stems if name.lower().endswith("output") and name not in timers]
    # What a kernel output clocks, by how sure the name is: the macros of its own
    # mux (RCC_SDMMC1CLKSOURCE_PLL1), its node name, its enable flags (Tim3Enable,
    # EnableDFSDM) and its parameter (SPDIFRXFreq_Value). The U5 spells the SDMMC
    # output SDMMCC1Output, the L0 the LPUART1 output LPUARTOutput.
    named = [[], [], [], []]
    for name in outputs:
        if name in timers:
            continue
        node = nodes[name]
        muxes = [nodes[i["from"]] for i in node["inputs"] if nodes.get(i["from"], {}).get("kind") == "mux"]
        # A mux that feeds several outputs names neither alone, the STM32H7 SAI1 mux also clocks DFSDM1
        for mux in (m for m in muxes if sum(nodes.get(o, {}).get("kind") == "output" for o in m["outputs"]) == 1):
            for input in mux["inputs"]:
                if found := re.match(r"RCC_(\w+?)CLK(?:SOURCE)?_", input.get("macro", "")):
                    named[0].append((found[1].upper(), name))
        named[1] += [(stem.upper(), n) for n, stem in stems if n == name]
        if re.match(r"(PLL|DIV)", name):
            # A PLL output is enabled for its consumers, it does not clock them alone
            continue
        for flag in filter(None, node.get("enable", "").split(",")):
            named[2].append((re.sub(r"^Enable|Enable$", "", flag).upper(), name))
        if found := re.fullmatch(r"(\w+?)Freq_V(?:alue|ALUE)", node.get("param", "")):
            named[3].append((found[1].upper(), name))
    # A stem without a number stands for the whole group, ADCEnable for ADC1..4
    generic = {}
    for tier in named:
        for stem, name in tier:
            # Not the shared spellings: I2S1output does not clock SPI1
            for spelling in sum(_spellings(stem, shared=False), []):
                muxed.setdefault(spelling, name)
            if not re.search(r"\d$", stem):
                generic.setdefault(stem, name)
    # The STM32H7 muxes groups of instances, USART16output clocks USART1 and USART6,
    # I2C123output I2C1..3, unless the group is an instance itself like TIM15
    upper = {i.upper() for i in instances}

    def members(name: str) -> list[str]:
        """USART16 stands for USART1 and USART6, ADC345 for ADC3..5, but TIM15 for itself."""
        if (match := re.fullmatch(r"(\w*?[A-Z])(\d{2,})", name.upper())) and name.upper() not in upper:
            return [match[1] + digit for digit in match[2]]
        return []

    for tier in named:
        for stem, name in tier:
            for member in members(stem):
                muxed.setdefault(member, name)

    # The header enables groups too, for example, ADC12EN on the STM32G4 and STM32H7,
    # but TIM17EN is a timer of another device, not TIM1 and TIM7
    def grouped(g):
        return re.match(r"(ADC|DAC|COMP|OPAMP)\d", g.upper())

    header_buses = {
        bus: {*group, *(m for g in group if grouped(g) for m in members(g))} for bus, group in header_buses.items()
    }

    def find(names: list[str]) -> str:
        """The node that clocks a peripheral under any of these spellings."""
        for spelling in names:
            if spelling in muxed:
                return muxed[spelling]
        # A timer runs from the x1/x2 multiplier of the bus its enable bit is in,
        # whatever CubeMX lists: the STM32H7 lists TIM1 for the APB1 timer clock
        for spelling in names:
            if not re.fullmatch(r"TIM\d+", spelling):
                continue
            for bus, group in header_buses.items():
                if bus in bus_node and spelling in group and (output := timer_output(bus_node[bus])):
                    return output
        for spelling in names:
            for node, group in buses.items():
                if spelling in {g.upper() for g in group}:
                    # The enable bit decides the bus, the STM32U3 drawing puts OPAMP1 on APB3 instead of APB1
                    enabled = [bus_node[b] for b, g in header_buses.items() if b in bus_node and spelling in g]
                    if enabled and enabled[0] != node and re.match(r"(AHB|APB)", node):
                        return enabled[0]
                    return node
        for spelling in names:
            for bus, group in header_buses.items():
                if bus in bus_node and spelling in {g.upper() for g in group}:
                    return bus_node[bus]
        return None

    def by_address(names: list[str]) -> str:
        """A block without an enable bit runs from the bus of its address, for
        example, the STM32U5 ICACHE or the STM32H7 RAMECC1..3."""
        for spelling in names:
            found = [
                b for n, b in sorted(base_buses.items()) if n == spelling or re.fullmatch(rf"{spelling}(\d|_\w+)", n)
            ]
            if found and found[0] in bus_node:
                return bus_node[found[0]]
        return None

    attached = {}
    for peripheral in sorted(instances):
        exact, loose = _spellings(peripheral)
        source = (
            next((muxed[s] for s in exact if s in muxed), None)
            # Timers follow their timer clock, not a generic TIMOutput
            or next((generic[s] for s in loose if s in generic and s != "TIM"), None)
            or find(exact)
            or find(loose)
            or by_address(exact + loose)
        )
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
        # A peripheral with a kernel clock of its own still needs the clock of its bus
        bus = next(
            (
                bus_node[b]
                for name in exact + loose
                for b, group in header_buses.items()
                # The header may spell a shared bit, for example, I2C1_I3C1EN on the STM32H7R/S
                if b in bus_node and (name in group or any(g.startswith(name + "_") for g in group))
            ),
            None,
        )
        if bus is not None and bus != source:
            nodes[peripheral]["bus"] = bus
    return attached
