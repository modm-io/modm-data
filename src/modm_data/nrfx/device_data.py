# Copyright 2020, Hannes Ellinger
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from pathlib import Path

from ..kg import DeviceIdentifier
from ..utils import ext_path, XmlReader
from .pinout import pinout

LOGGER = logging.getLogger(__name__)
_MDK_PATH = ext_path("nordic/nrfx/bsp/stable/mdk")


def did_from_string(string: str) -> DeviceIdentifier:
    """
    Parses NRF device strings, for example, `nrf52840-qiaa`, organized as
    `{platform}{family}{series}-{package}{function}` and optionally `@{core}`.
    """
    string = string.lower()
    match_string = r"nrf(?P<family>[0-9]{2})(?P<series>[0-9]{2,3})-(?P<package>\w{2})(?P<function>\w{2})"
    if "nrf53" in string:
        match_string += r"-(?P<core>\w+)"
    if string.startswith("nrf") and (match := re.search(match_string, string)):
        i = DeviceIdentifier("{platform}{family}{series}-{package}{function}")
        i.set("platform", "nrf")
        i.set("family", match.group("family").lower())
        i.set("series", match.group("series").lower())
        i.set("package", match.group("package").lower())
        i.set("function", match.group("function").lower())
        if "nrf53" in string:
            i.naming_schema += "@{core}"
            i.set("core", match.group("core").lower()[:3])
        return i
    raise ValueError(f"Unknown identifier '{string}'!")


def device_files(prefix: str) -> list[Path]:
    """
    :param prefix: A device prefix, for example, `nrf52`.
    :return: A sorted list of linker scripts of the devices in the nrfx MDK.
    """
    return sorted(_MDK_PATH.rglob(f"{prefix.lower()}[0-9]*_*.ld"), key=lambda p: p.name)


def device_from_file(ld_path: Path) -> dict:
    """
    Extracts the device data from the nrfx MDK linker script and SVD file.

    :param ld_path: Path to the linker script of the device.
    :return: A dictionary of device properties.
    """
    ld_path = Path(ld_path)
    p = {"id": (did := did_from_string(ld_path.stem.replace("_", "-")))}

    svd_path = ld_path.with_name(re.sub(r"\_\w{4}(\_\w+)?.ld", r"\1.svd", ld_path.name))
    if not svd_path.exists():
        fallback = {"51": "nrf51.svd", "52": "nrf52.svd"}.get(did.family)
        for candidate in [ld_path.with_name(fallback), ld_path.parent.with_name(fallback)] if fallback else []:
            if candidate.exists():
                svd_path = candidate
                break
    device_file = XmlReader(svd_path)
    LOGGER.info("Parsing '%s'", did.string)

    # information about the core and architecture
    core = device_file.query("//device/cpu/name")[0].text.lower().replace("cm", "cortex-m")
    if device_file.query("//device/cpu/fpuPresent")[0].text in ("1", "true"):
        p["fpu"] = "fpv4-sp-d16" if "m4" in core else "fpv5-sp-d16"
    p["core"] = core
    p["revision"] = device_file.query("//device/cpu/revision")[0].text

    # find the values for flash and ram
    memories = []
    if did.family == "53":
        if did.core == "app":
            memories = [{"name": "flash", "access": "rx", "size": str(1024 * 1024), "start": "0x00000000"}]
            for idx in range(8):
                start = hex(0x20000000 + idx * 64 * 1024)
                memories.append({"name": f"ram{idx}", "access": "rwx", "size": str(64 * 1024), "start": start})
        else:
            memories = [{"name": "flash", "access": "rx", "size": str(256 * 1024), "start": "0x01000000"}]
            for idx in range(4):
                start = hex(0x21000000 + idx * 16 * 1024)
                memories.append({"name": f"ram{idx}", "access": "rwx", "size": str(16 * 1024), "start": start})
    else:
        memory = re.search(r"MEMORY\s*\{(.*?)\}", ld_path.read_text(), flags=re.DOTALL).group(1)
        pattern = (
            r"  (?P<name>\w+) \((?P<access>\w+)\) : ORIGIN = (?P<start>0x[\da-fA-F]+), LENGTH = (?P<size>0x[\da-fA-F]+)"
        )
        for match in re.finditer(pattern, memory):
            name = match.group("name").lower()
            if "ext" in name:
                continue
            memories.append(
                {
                    "name": name.replace("code_ram", "code"),
                    "access": match.group("access").lower(),
                    "size": str(int(match.group("size").lower(), 16)),
                    "start": match.group("start").lower(),
                }
            )
    p["memories"] = memories

    # Signals
    signals = {}
    for s in device_file.query("//peripherals/peripheral/registers/cluster"):
        if s.find("name").text == "PSEL":
            instance = s.getparent().getparent().find("name").text.lower().split("_")[0]
            signals[instance] = [element.text.lower() for element in s.findall("register/name")]

    # nRF51 and older SVD files may use PSEL* registers directly instead of a PSEL cluster
    for peripheral in device_file.query("//peripherals/peripheral"):
        instance = peripheral.find("name").text.lower().split("_")[0]
        for register_name in peripheral.findall("registers/register/name"):
            if register_name.text is None or not register_name.text.startswith("PSEL"):
                continue
            if (signal_name := register_name.text[4:].lower()) == "":
                continue
            signals.setdefault(instance, [])
            if signal_name not in signals[instance]:
                signals[instance].append(signal_name)

    # drivers and gpios
    modules = []
    ports = {}
    gpios = []
    fixed_signals = {}
    for m in device_file.query("//peripherals/peripheral"):
        modulename = m.find("name").text
        if modulename.endswith("_S"):
            continue
        modulename = modulename.split("_")[0]

        if "GPIO Port" in m.find("description").text or modulename == "GPIO":
            # omit the leading P of the port names, also of the derived ports
            portnumber = "0" if modulename == "GPIO" else modulename[1:]
            if m.get("derivedFrom") is not None:
                portsize = ports[m.get("derivedFrom")[1:].split("_")[0]]
            else:
                portsize = int(m.find("size").text, base=0)
            ports[portnumber] = portsize
            gpios.extend((portnumber, str(i)) for i in range(portsize))
            continue

        module = re.search(r"(?P<module>.*\D)(?P<instance>\d*$)", modulename).group("module").lower()
        modules.append((module, modulename.lower()))

        # copy available signals to all derived peripherals
        if m.get("derivedFrom") is not None and m.get("derivedFrom").lower() in signals:
            signals[modulename.lower()] = signals[m.get("derivedFrom").lower()]

        # extract fixed analog channel capabilities from enum descriptions (AIN0..AINx)
        for field in m.findall("registers//field"):
            field_name = field.find("name")
            if (
                field_name is None
                or field_name.text is None
                or field_name.text.upper() not in ("PSEL", "PSELP", "PSELN")
            ):
                continue
            for enum_value in field.findall("enumeratedValues/enumeratedValue"):
                enum_name = enum_value.find("name").text if enum_value.find("name") is not None else ""
                enum_desc = enum_value.find("description").text if enum_value.find("description") is not None else ""
                for token in (enum_name, enum_desc):
                    if token is not None and (match := re.search(r"AIN(?P<index>\d+)", token.upper())):
                        fixed_signals.setdefault(module, set()).add(f"ain{match.group('index')}")

    p["modules"] = sorted(list(set(modules)))
    p["gpios"] = gpios
    p["fixed_signals"] = {module: sorted(names) for module, names in fixed_signals.items()}
    p["signals"] = []
    for instance, names in signals.items():
        driver = re.search(r"(?P<module>.*\D)(?P<instance>\d*$)", instance).group("module").lower()
        # TODO take care of multichannel signals like OUT[%s] of PWM peripheral
        p["signals"].extend({"driver": driver, "instance": instance, "name": n} for n in names if "[%s]" not in n)

    pin_data = pinout(f"nrf{did.family}{did.series}")
    p["pin_packages"] = pin_data["packages"]
    p["pin_specials"] = pin_data["specials"]

    # Unique interrupts
    p["interrupts"] = []
    for i in device_file.query("//peripherals/peripheral/interrupt"):
        interrupt = {"position": i.find("value").text, "name": i.find("name").text}
        if interrupt not in p["interrupts"]:
            p["interrupts"].append(interrupt)
    return p
