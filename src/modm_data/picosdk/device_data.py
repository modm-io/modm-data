# Copyright 2022, Andrey Kunitsyn
# SPDX-License-Identifier: MPL-2.0

import re
import logging
from pathlib import Path

from ..kg import DeviceIdentifier
from ..utils import ext_path, XmlReader

LOGGER = logging.getLogger(__name__)
_SVD_PATH = ext_path("raspberrypi/svd")


def did_from_string(string: str) -> DeviceIdentifier:
    """
    Parses RP device strings, for example, `rp2040`, organized as
    `{platform}{cores}{type}{ram}{flash}`.
    """
    string = string.lower()
    if not string.startswith("rp"):
        raise ValueError(f"Unknown identifier '{string}'!")
    i = DeviceIdentifier("{platform}{cores}{type}{ram}{flash}")
    i.set("platform", "rp")
    i.set("cores", string[2])
    i.set("type", string[3])
    i.set("ram", string[4])
    i.set("flash", string[5])
    i.set("family", string[2:4])
    return i


def device_files(prefix: str) -> list[Path]:
    """
    :param prefix: A device prefix, for example, `rp2040`.
    :return: A sorted list of SVD files matching the prefix.
    """
    return sorted(_SVD_PATH.glob(f"{prefix.lower()}*.svd"))


def _func_to_signal(modules, func):
    parts = func["name"].split("_")
    idx = 0
    name = parts[idx]
    while name not in modules:
        idx = idx + 1
        if idx >= len(parts):
            raise ValueError(f"Not found module for func {func}")
        name = name + "_" + parts[idx]
    m = modules[name]
    eidx = len(parts) - 1
    while re.match(r"^(\d)+$", parts[eidx]) and eidx > idx:
        eidx = eidx - 1
    res = {"driver": m["module"], "name": "_".join(parts[idx + 1 : eidx + 1]), "af": func["value"]}
    if res["name"] == "":
        res["name"] = "pad"  # default name
    if m["module"] != m["instance"]:
        res["instance"] = m["instance"][len(m["module"]) :]
    return res


def _clock_source_name(name):
    if name.startswith("clksrc_"):
        return name[7:]
    if name.startswith("clk_"):
        return name[4:]
    if name.endswith("_clksrc"):
        return name[0:-7]
    if name.endswith("_clksrc_ph"):
        return name[0:-10]
    return name


def _gpio_funcs(ctrl):
    func = ctrl.find("./fields/field[name='FUNCSEL']")
    return [
        {"name": f.find("name").text, "value": f.find("value").text}
        for f in func.findall("./enumeratedValues/enumeratedValue")
        if f.find("name").text != "null"
    ]


def device_from_file(path: Path) -> dict:
    """
    Extracts the device data from an RP SVD file.

    :param path: Path to the SVD file.
    :return: A dictionary of device properties.
    """
    device_file = XmlReader(path)
    p = {"id": (did := did_from_string(Path(path).stem))}
    LOGGER.info("Parsing '%s'", did.string)

    # information about the core and architecture
    core = device_file.query("//device/cpu/name")[0].text.lower().replace("cm", "cortex-m")
    if device_file.query("//device/cpu/fpuPresent")[0].text in ("1", "true"):
        p["fpu"] = "fpv5-sp-d16"
    p["core"] = core
    p["revision"] = device_file.query("//device/cpu/revision")[0].text

    # TODO: Memories are not described in the SVD file
    p["memories"] = [
        {"name": "ram", "access": "rwx", "size": str(0x40000), "start": "0x20000000"},
        {"name": "core1", "access": "rwx", "size": str(0x1000), "start": "0x20040000"},
        {"name": "core0", "access": "rwx", "size": str(0x1000), "start": "0x20041000"},
    ]

    modules = [
        {"module": "jtag", "instance": "jtag"},
        {"module": "usb", "instance": "usb"},
        {"module": "xip", "instance": "xip"},
    ]
    gpios = []
    adc_channels = []
    dma_channels = []
    clocks = []
    for m in device_file.query("//peripherals/peripheral"):
        modulename = m.find("name").text
        if modulename == "IO_BANK0":
            for r in m.findall("./registers/register"):
                if r.find("description") is not None and r.find("description").text == "GPIO status":
                    name = re.search(r"^GPIO(?P<name>.*)_STATUS$", r.find("name").text).group("name").lower()
                    ctrl = m.find("./registers/register[name='GPIO" + name + "_CTRL']")
                    gpios.append({"name": name, "bank": "bank0", "idx": int(name), "funcs": _gpio_funcs(ctrl)})
        elif modulename == "IO_QSPI":
            for r in m.findall("./registers/register"):
                if r.find("description") is not None and r.find("description").text == "GPIO status":
                    name = re.search(r"^GPIO_QSPI_(?P<name>.*)_STATUS$", r.find("name").text).group("name").lower()
                    ctrl = m.find("./registers/register[name='GPIO_QSPI_" + name.upper() + "_CTRL']")
                    idx = int(int(r.find("addressOffset").text[2:], 16) / 8)
                    gpios.append({"name": name, "idx": idx, "bank": "qspi", "funcs": _gpio_funcs(ctrl)})
        elif modulename == "ADC":
            # TODO: Find way to get ADC channels info
            adc_channels = [
                {"id": 0, "name": "Ch0"},
                {"id": 1, "name": "Ch1"},
                {"id": 2, "name": "Ch2"},
                {"id": 3, "name": "Ch3"},
                {"id": 4, "name": "Temperature"},
            ]
            modules.append({"module": "adc", "instance": "adc"})
        elif modulename == "DMA":
            for r in m.findall("./registers/register"):
                if match := re.search(r"^CH(?P<name>\d+)_READ_ADDR$", r.find("name").text):
                    dma_channels.append({"name": match.group("name").lower()})
        elif modulename == "CLOCKS":
            for r in m.findall("./registers/register"):
                if match := re.search(r"^CLK_(?P<name>.+)_CTRL$", r.find("name").text):
                    name = match.group("name").lower()
                    aux_fld = r.find("./fields/field[name='AUXSRC']")
                    if aux_fld is None:
                        continue
                    idx = int(int(r.find("addressOffset").text, 16) / 12)
                    sources = []
                    aux_sel = 0
                    src_fld = r.find("./fields/field[name='SRC']")
                    if src_fld is not None:
                        for f in src_fld.findall("./enumeratedValues/enumeratedValue"):
                            src_name = f.find("name").text
                            if src_name == "clksrc_clk_" + name + "_aux":
                                aux_sel = f.find("value").text
                            else:
                                sources.append(
                                    {"name": _clock_source_name(src_name), "src": f.find("value").text, "aux": 0}
                                )
                    for f in aux_fld.findall("./enumeratedValues/enumeratedValue"):
                        sources.append(
                            {
                                "name": _clock_source_name(f.find("name").text),
                                "src": aux_sel,
                                "aux": f.find("value").text,
                            }
                        )
                    clocks.append({"name": name, "sources": sources, "glitchless": src_fld is not None, "idx": idx})
        else:
            match = re.search(r"(?P<module>.*\D)(?P<instance>\d*$)", modulename)
            modules.append({"module": match.group("module").lower(), "instance": modulename.lower()})

    p["modules"] = sorted(list(set([(m["module"], m["instance"]) for m in modules])))
    p["gpios"] = gpios
    p["adc_channels"] = adc_channels
    p["dma_channels"] = dma_channels
    p["clocks"] = clocks

    modules_map = {"clocks": {"module": "clocks", "instance": "clocks"}}
    for m in modules:
        modules_map[m["instance"]] = m

    # Manually patch this here instead of the SVD file
    adc_map = {
        "bank026": {"driver": "adc", "name": "in0", "af": "-1"},
        "bank027": {"driver": "adc", "name": "in1", "af": "-1"},
        "bank028": {"driver": "adc", "name": "in2", "af": "-1"},
        "bank029": {"driver": "adc", "name": "in3", "af": "-1"},
    }
    for gpio in gpios:
        gpio["signals"] = [_func_to_signal(modules_map, func) for func in gpio["funcs"]]
        if adcsig := adc_map.get(gpio["bank"] + gpio["name"]):
            gpio["signals"].append(adcsig)

    # Unique interrupts
    p["interrupts"] = []
    for i in device_file.query("//peripherals/peripheral/interrupt"):
        interrupt = {"position": i.find("value").text, "name": i.find("name").text}
        if interrupt not in p["interrupts"]:
            p["interrupts"].append(interrupt)
    return p
