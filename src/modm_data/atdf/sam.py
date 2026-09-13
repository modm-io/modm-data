# Copyright 2013, Niklas Hauser
# Copyright 2016, Fabian Greif
# Copyright 2022, Christopher Durand
# SPDX-License-Identifier: MPL-2.0

import logging
from pathlib import Path
from collections import defaultdict

from ..utils import ext_path, XmlReader
from .identifier import sam_did_from_string

LOGGER = logging.getLogger(__name__)
_ATDF_PATH = ext_path("microchip/sam")


def device_files(prefix: str) -> list[Path]:
    """
    :param prefix: A SAM device prefix, for example, `samd21` or `same7`.
    :return: A sorted list of ATDF files matching the prefix.
    """
    return sorted(_ATDF_PATH.glob(f"*/AT{prefix.upper()}*"))


def devices_from_file(path: Path) -> list[str]:
    """:return: A sorted list of device order codes described in the ATDF file."""
    device_file = XmlReader(path)
    return sorted(set(d for d in device_file.query("//variants/variant/@ordercode") if d != "standard"))


def device_from_ordercode(path: Path, ordercode: str) -> dict:
    """
    Extracts the device data of one order code from a SAM ATDF file.

    :param path: Path to the ATDF file.
    :param ordercode: The device order code, for example, `ATSAMD21G18A-AU`.
    :return: A dictionary of device properties.
    """
    p = {}

    device_file = XmlReader(path)
    variant = device_file.query(f'//variants/variant[@ordercode="{ordercode}"]')[0]
    p["id"] = did = sam_did_from_string(ordercode.lower())
    LOGGER.info("Parsing '%s'", did.string)

    # Package information
    p["package"] = variant.get("package")
    p["pinout"] = variant.get("pinout")
    p["pinout_pins"] = {
        pin.get("position"): pin.get("pad") for pin in device_file.query(f'//pinouts/pinout[@name="{p["pinout"]}"]/pin')
    }

    # information about the core and architecture
    p["core"] = core = device_file.query("//device")[0].get("architecture").lower()
    fpu, dp = False, False
    for param in device_file.query("//device/parameters")[0]:
        name, value = param.get("name"), param.get("value")
        if name == "__FPU_PRESENT" and value == "1":
            fpu = True
        if name == "__FPU_DP" and value == "1":
            dp = True
        if name.startswith("__CM") and name.endswith("_REV"):
            rev = int(value, 0)
            p["revision"] = f"r{rev >> 8}p{rev & 0xFF}"
    if fpu:
        p["fpu"] = "fpv4-sp-d16" if "m4" in core else ("fpv5-d16" if dp else "fpv5-sp-d16")

    # find the values for flash, ram and (optional) eeprom
    memories = []
    for memory_segment in device_file.query("//memory-segment"):
        name = memory_segment.get("name")
        start = memory_segment.get("start")
        size = int(memory_segment.get("size"), 16)
        access = memory_segment.get("rw", "r").lower()
        if memory_segment.get("exec") == "true":
            access += "x"
        if name in ["FLASH", "IFLASH"]:
            memories.append({"name": "flash", "access": "rx", "size": str(size), "start": start})
        elif name in ["HMCRAMC0", "HMCRAM0", "HSRAM", "IRAM"]:
            memories.append({"name": "ram", "access": access, "size": str(size), "start": start})
        elif name in ["LPRAM", "BKUPRAM"]:
            memories.append({"name": "lpram", "access": access, "size": str(size), "start": start})
        elif name in ["SEEPROM", "RWW"]:
            memories.append({"name": "eeprom", "access": "r", "size": str(size), "start": start})
        else:
            LOGGER.debug("Memory segment '%s' not used", name)
    p["memories"] = memories

    modules = []
    p["gclk_data"] = {"clocks": defaultdict(list)}
    p["dma_requests"] = defaultdict(list)
    for m in device_file.query("//peripherals/module/instance"):
        module_name = m.getparent().get("name").lower()
        instance = m.get("name").lower()
        for param in m.xpath("parameters/param"):
            name = param.get("name")
            if name.startswith("GCLK_ID"):
                clock_name = name[8:].lower()
                p["gclk_data"]["clocks"][instance].append(
                    (clock_name if clock_name != "" else None, param.get("value"))
                )
            if name.startswith("DMAC_ID_"):
                signal = "_".join(name.lower().split("_")[2:])
                p["dma_requests"][instance].append((signal, int(param.get("value"))))

        if module_name == "gclk":
            p["gclk_data"]["generator_count"] = int(m.xpath('parameters/param[@name="GEN_NUM"]')[0].attrib["value"])
        if module_name != "port":
            modules.append((module_name, instance))
    p["modules"] = sorted(list(set(modules)))

    # parse GCLK sources from register section
    generators = device_file.query('//modules/module[@name="GCLK"]/value-group[@name="GCLK_GENCTRL__SRC"]/value')
    p["gclk_data"]["sources"] = dict([(g.get("name").capitalize(), g.get("value")) for g in generators])

    signals = []
    gpios = []
    for s in device_file.query("//peripherals/module/instance/signals/signal"):
        tmp = {
            "module": s.getparent().getparent().getparent().get("name").lower(),
            "instance": s.getparent().getparent().get("name").lower(),
        }
        tmp.update({k: v.lower() for k, v in s.items()})
        if "group" in tmp:
            tmp["group"] = tmp["group"].replace(f"{tmp['instance']}_", "")

        # Fix duplicate GPIO data for SAMx7x revision A devices
        # FIXME: The family is lower case, so this fix is never applied!
        if did.family == "E7x/S7x/V7x" and did.variant == "a":
            if tmp["module"] in ("sdramc", "smc"):
                continue

        if tmp["group"] in ["p", "pin"] or tmp["group"].startswith("port"):
            gpios.append(tmp)
        else:
            signals.append(tmp)
    gpios = sorted([(g["pad"][1], g["pad"][2:]) for g in gpios])

    p["signals"] = signals
    # Filter gpios by pinout
    p["gpios"] = [pin for pin in gpios if f"P{pin[0].upper()}{pin[1]}" in p["pinout_pins"].values()]
    p["interrupts"] = [
        {"position": i.get("index"), "name": i.get("name")} for i in device_file.query("//interrupts/interrupt")
    ]

    # Events pass data from source to sink without waking the processor
    p["event_sources"] = [
        {"index": i.get("index"), "name": i.get("name"), "instance": i.get("module-instance")}
        for i in device_file.query("//events/generators/generator")
    ]
    p["event_users"] = [
        {"index": i.get("index"), "name": i.get("name"), "instance": i.get("module-instance")}
        for i in device_file.query("//events/users/user")
    ]
    return p
