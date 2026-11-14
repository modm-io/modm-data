# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import json
import logging
from functools import cache
from collections import defaultdict
from lxml import etree

from ..kg.stmicro import did_from_string
from ..utils import ext_path
from ..header2svd.stmicro import Header
from ..cubemx import stm32_data
from ..cubemx.memories import memories as device_memories
from ..cubemx.device_data import header_modules, dma3_requests, split_signals
from ..cubeprog import flash_layouts

LOGGER = logging.getLogger(__file__)

_HAL2_PATH = ext_path("stmicro/hal2")
_CUBE_PATH = ext_path("stmicro/cubehal")
# Device prefix to the name of the CMSIS family header, the CubeHAL folder and the DFP repository
_FAMILIES = {"STM32C5": "stm32c5xx", "STM32G5": "stm32g5xx", "STM32H7P": "stm32h7pxx"}


def _family(partname: str) -> str | None:
    return next((f for p, f in _FAMILIES.items() if partname.upper().startswith(p)), None)


def is_hal2_device(partname: str) -> bool:
    """:return: True if the device is described by a HAL2 device family pack instead of CubeMX."""
    return _family(partname) is not None


@cache
def _variants() -> dict[str, list]:
    """:return: The `<variant>` nodes of all HAL2 device family packs by CubeMX-style device name."""
    variants = defaultdict(list)
    for family in _FAMILIES.values():
        if not (packs := sorted((_HAL2_PATH / f"{family}-dfp").glob("*.pdsc"))):
            LOGGER.warning(f"No device family pack found for {family}!")
            continue
        for variant in etree.parse(packs[0]).iter("variant"):
            # The ordering code without the tape and reel suffix, with the temperature as placeholder
            code = variant.get("Dvariant").removesuffix("TR")
            variants[code[:12] + "x" + code[13:]].append(variant)
    return variants


def devices_from_prefix(prefix: str) -> list[str]:
    """
    :param prefix: A STM32 device prefix, for example, STM32, STM32C5, STM32G553.
    :return: A list of device names starting with the prefix.
    """
    prefix = prefix.upper()
    return sorted(d for d in _variants() if d.startswith(prefix) and not stm32_data.ignoreDevice(d))


def temperature_codes(partname: str) -> list[str]:
    """:return: The sorted temperature codes of all ordering codes of the device."""
    return sorted({v.get("Dvariant")[12] for v in _variants()[partname]})


def _name_pattern(names: str) -> str:
    """
    Converts device names with alternatives into a regex, for example,
    `STM32C5(5-6)2R(C-E)Tx(J-_)`, where `_` stands for no character.
    """
    return re.sub(r"\(([^)]*)\)", lambda m: "(?:" + "|".join(c.strip("_") for c in m.group(1).split("-")) + ")", names)


def _descriptor(family: str, kind: str, partname: str) -> dict | None:
    """Finds the descriptor of the device, for example, `STM32C5(5-6)2R(C-E)Tx(J-_)_pinout.json`."""
    for path in sorted((_HAL2_PATH / f"{family}-dfp/Descriptors" / kind).glob("*.json")):
        if re.fullmatch(_name_pattern(path.stem.rsplit("_", 1)[0]), partname):
            return json.loads(path.read_text())
    return None


@cache
def _die_descriptor(family: str, die: str, kind: str) -> dict:
    return json.loads((_HAL2_PATH / f"{family}-dfp/Descriptors" / f"{kind}s" / f"D{die}_{kind}s.json").read_text())


@cache
def _dma_request_map(family: str, controller: str) -> dict[str, int]:
    """:return: The request numbers of the DMA controller type from the LL header, for example, `lpdma`."""
    content = (_CUBE_PATH / family / "Inc" / f"{family}_ll_dma.h").read_text()
    pattern = rf"#define\s+LL_{controller.upper()}1_REQUEST_(\w+)\s+(\d+)U"
    # The H7P names the UCPD1 requests after the USBPD, which is not a peripheral of the device
    return {name.replace("USBPD_", "UCPD1_"): int(number) for name, number in re.findall(pattern, content)}


_PIN_TYPES = {"gpio": "I/O", "power_supply": "Power", "reset": "Reset", "boot": "Boot"}
# Peripherals that are not hardware modules for modm
_IGNORED_TYPES = ("bus_interconnect", "fdcanram")


def _modules(peripherals: list[dict], removed: set[str]) -> list[tuple]:
    """
    Converts the peripherals of the die into CubeMX-style (name, instance, version)
    modules. Peripherals that group several instances, like ADC12 or RTC_TAMP, are
    replaced by their instances, while their sub-blocks, like DAC1_CH1, are ignored.
    """

    def parent(p):
        return p.get("parent", ".").split(".", 1)[1]

    def is_instance_block(p):
        return parent(p) and not p["name"].startswith(parent(p) + "_") and not p["peripheralType"].startswith("fmc_")

    groups = {parent(p) for p in peripherals if is_instance_block(p)}
    modules = set()
    for p in peripherals:
        name = p["name"]
        if p["peripheralType"] in _IGNORED_TYPES or name in groups or name in removed:
            continue
        if parent(p) and not is_instance_block(p):
            continue
        version = f"{p['digitalName']}-v{p['peripheralVersionNum']}".lower()
        if p["peripheralType"] == "cortex":
            # The debug signals are named after the DEBUG peripheral, like in CubeMX
            modules.add(("debug", "debug", version))
        elif p["peripheralType"] == "gpio":
            modules.add(("gpio", "gpio", version))
        elif name.startswith("RAMCFG"):
            modules.add(("ramcfg", "ramcfg", version))
        else:
            modules.add((re.sub(r"\d+$", "", name).lower(), name.lower(), version))
        # The SPI peripherals with I2S support have separate I2S signals, like in CubeMX
        if any(protocol["type"] == "I2S" for protocol in p.get("protocols", [])):
            modules.add(("i2s", name.lower().replace("spi", "i2s"), version))
    return sorted(modules)


def devices_from_partname(partname: str) -> list[dict[str]]:
    """
    Find the STM32 device name in the HAL2 device family packs and convert the
    data into the same device specific key-value mapping as the CubeMX database.

    :param partname: A full STM32 device name, for example, `STM32C551CCTx`.
    :return: a list of dictionaries containing a device specific data structure.
    """
    family = _family(partname)
    variants = _variants()[partname]
    did = did_from_string(partname.lower())
    LOGGER.info(f"Parsing '{did.string}'")

    pinout = _descriptor(family, "pinout", partname)
    if pinout is None:
        LOGGER.error(f"No pinout descriptor found for {did.string}: {partname}")
        return []
    die = pinout["characteristics"]["die_name"]
    p = {"id": did, "die": f"DIE{die}"}

    def dfp_findall(key, attribs=None):
        values = []
        node = variants[0]
        while node.tag != "devices":
            values += node.findall(key)
            node = node.getparent()
        if attribs is not None:
            # The attributes of the inner nodes take precedence over the outer nodes
            values = {a: v.get(a) for v in reversed(values) for a in attribs if v.get(a)}
        return values

    p["cmsis_header"] = family
    header = Header(did, family, dfp_findall("compile")[0].get("define"))
    if not header.is_valid:
        LOGGER.error("CMSIS Header invalid for %s", did.string)
        return []
    p["define"] = header.define
    p["interrupts"] = header.interrupt_table

    processor = dfp_findall("processor", ["Dcore", "DcoreVersion", "Dclock", "Dfpu"])
    p["core"] = core = processor["Dcore"].lower()
    if (fpu := processor.get("Dfpu")) == "SP_FPU":
        p["fpu"] = "fpv4-sp-d16" if "m4" in core else "fpv5-sp-d16"
    elif fpu == "DP_FPU":
        p["fpu"] = "fpv5-d16"
    if rev := processor.get("DcoreVersion"):
        p["revision"] = rev.lower()
    p["max_frequency"] = int(processor["Dclock"])

    memories = {
        m.get("name").lower(): {
            # The DFP marks the non-secure and secure memories with `n` and `s`
            "access": m.get("access", "rwx").replace("n", "").replace("s", ""),
            "start": int(m.get("start"), 0),
            "size": int(m.get("size"), 0),
            "alias": m.get("alias", "").lower(),
        }
        for m in dfp_findall("memory")
    }
    # The DFP lists some SRAMs twice, as a whole and as parts, for example, SRAM and SRAM2_3
    memories = {
        name: m
        for name, m in memories.items()
        if not any(
            o is not m and o["start"] <= m["start"] and m["start"] + m["size"] <= o["start"] + o["size"]
            for o in memories.values()
        )
    }
    if did.family == "h7":
        # The DFP of the STM32H7P only lists the DTCM as SRAM, the CMSIS header has all memories
        def region(name):
            return {
                "access": "rwx",
                "start": header.define_value(f"{name}_BASE"),
                "size": header.define_value(f"{name}_SIZE"),
            }

        memories.pop("sram")
        memories |= {"itcm": region("ITCM"), "dtcm": region("DTCM")}
        # The AXI SRAMs are contiguous like the D1 SRAM of the STM32H7R/S
        axi = [region(f"SRAM{i}_AXI") for i in range(1, 5)]
        memories["d1_sram"] = axi[0] | {"size": sum(r["size"] for r in axi)}
        memories |= {f"d2_sram{i}": region(f"SRAM{i}_AHB") for i in range(1, 4)}
    memories = device_memories(did, memories, header, p["die"])
    # The secure alias of a split SRAM has no non-secure memory left to refer to
    names = {m["name"] for m in memories}
    p["memories"] = [m for m in memories if m.get("alias", m["name"]) in names]

    # The DFP has no flash algorithms, so the flash organization is only from the CubeProgrammer
    if flash := [m for m in p["memories"] if m["name"].startswith("flash") and not m.get("alias")]:
        regions = tuple(sorted((m["start"], m["size"]) for m in flash))
        p["flash_layouts"] = flash_layouts(p["die"], sum(m["size"] for m in flash), (), regions)

    p["package"] = pinout["characteristics"]["package_name"]
    p["pin-count"] = re.findall(r"[0-9]+", p["package"])[0]

    # Peripherals that are removed in all ordering codes of this device
    limitations = _die_descriptor(family, die, "limitation")["limitations"]
    removed = set.intersection(
        *(set(limitations.get(v.get("Dvariant"), {}).get("removed_peripherals", [])) for v in variants)
    )
    modules = _modules(_die_descriptor(family, die, "peripheral")["peripherals"], removed)
    p["modules"] = modules = header_modules(did, modules, header)

    p["flash_latency"] = stm32_data.getFlashLatencyForDevice(did)

    # The DMA descriptor lists the requests in order without numbers, the LL header has the numbers
    for controller in sorted({m[0] for m in modules if m[0] in ("gpdma", "hpdma", "lpdma")}):
        p[controller] = {0: {0: dma3_requests(_dma_request_map(family, controller), modules, controller)}}
        p[controller + "_naming"] = (None, "request", "signal")

    pads = pinout["die_pads"]
    p["pinout"] = [
        {
            "name": bond["die_pad"],
            "position": bond["position"],
            "type": _PIN_TYPES.get(
                pads[bond["die_pad"]]["type"], _PIN_TYPES.get(pads[bond["die_pad"]].get("function"), "I/O")
            ),
        }
        for bond in pinout["bonds"]
    ]

    pad_signals = defaultdict(list)
    for signal in pinout["signals"]:
        if (function := signal["function"])["type"] != "user_gpio":
            af = function["id"].removeprefix("AF") if function["type"] == "alternate" else None
            pad_signals[signal["die_pad"]].append((signal["name"].lower(), af))

    gpios = []
    signals = set()
    bonded = {bond["die_pad"] for bond in pinout["bonds"]}
    for name, pad in pads.items():
        if pad["type"] != "gpio" or name not in bonded:
            continue
        afs = []
        for signal, af in pad_signals[name]:
            for driver, instance, sname in split_signals(signal, modules):
                if driver is None:
                    LOGGER.debug(f"Ignoring signal without peripheral: {name} {signal}")
                    continue
                afs.append({"driver": driver, "instance": instance, "name": sname, "af": af})
                signals.add(sname)
        gpios.append((pad["port"].lower(), str(pad["index"]), afs))

    p["remaps"] = {}
    p["gpios"] = sorted(gpios, key=lambda g: (g[0], int(g[1])))
    p["signals"] = signals
    return [p]
