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
    Parses RP device strings, for example, `rp2040` or `rp2350a`, organized as
    `{platform}{cores}{type}{ram}{flash}{package}`. The package letter only
    exists for the RP2350 family: A is the QFN-60 with 30 GPIOs, B the QFN-80
    with 48 GPIOs.
    """
    string = string.lower()
    if not string.startswith("rp") or len(string) not in (6, 7):
        raise ValueError(f"Unknown identifier '{string}'!")
    schema = "{platform}{cores}{type}{ram}{flash}"
    if len(string) == 7:
        schema += "{package}"
    i = DeviceIdentifier(schema)
    i.set("platform", "rp")
    i.set("cores", string[2])
    i.set("type", string[3])
    i.set("ram", string[4])
    i.set("flash", string[5])
    i.set("family", string[2:4])
    if len(string) == 7:
        i.set("package", string[6])
    return i


# The SVD files describe the largest package of a family, so the devices and
# their package differences are listed here. RP2354 is the RP2350 with 2 MiB of
# flash stacked in the package, connected to the QSPI pins.
# Sources: RP2350 datasheet sections 1.2.3 (Pinout), 2.2 (Address map), 12.4.3
# (ADC Channels), pico-sdk src/rp2350/hardware_regs/include/hardware/platform_defs.h
_DEVICES = {
    "rp2040": [{"name": "rp2040", "gpios": 30, "adc_pin": 26, "adc_channels": 4}],
    "rp2350": [
        {"name": f"rp235{flash}{package}", "gpios": gpios, "adc_pin": adc_pin, "adc_channels": channels}
        for flash in "04"
        for package, gpios, adc_pin, channels in (("a", 30, 26, 4), ("b", 48, 40, 8))
    ],
}

# The SVD files do not describe memories. Main SRAM is striped over four (RP2040)
# or eight (RP2350) banks, followed by two 4 kiB banks used as core-local memory
# by the pico-sdk (SCRATCH_X for core 1, SCRATCH_Y for core 0).
_MEMORIES = {
    "20": (0x20000000, 0x40000),
    "23": (0x20000000, 0x80000),
}


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


def device_from_file(path: Path) -> list[dict]:
    """
    Extracts the device data from an RP SVD file.

    :param path: Path to the SVD file.
    :return: A list of device property dictionaries, one per device described
             by the SVD file.
    """
    device_file = XmlReader(path)
    p = _device_from_svd(device_file, did_from_string(Path(path).stem))
    devices = []
    for variant in _DEVICES[Path(path).stem.lower()]:
        d = dict(p)
        d["id"] = did_from_string(variant["name"])
        LOGGER.info("Parsing '%s'", d["id"].string)
        d["gpios"] = [dict(g) for g in p["gpios"] if g["bank"] != "bank0" or g["idx"] < variant["gpios"]]
        adc_pin, channels = variant["adc_pin"], variant["adc_channels"]
        d["adc_channels"] = [{"id": c, "name": f"Ch{c}"} for c in range(channels)]
        d["adc_channels"].append({"id": channels, "name": "Temperature"})
        for gpio in d["gpios"]:
            gpio["signals"] = [_func_to_signal(p["modules_map"], func) for func in gpio["funcs"]]
            if gpio["bank"] == "bank0" and 0 <= gpio["idx"] - adc_pin < channels:
                gpio["signals"].append({"driver": "adc", "name": f"in{gpio['idx'] - adc_pin}", "af": "-1"})
        del d["modules_map"]
        devices.append(d)
    return devices


def _device_from_svd(device_file, did) -> dict:
    p = {}

    # information about the core and architecture
    core = device_file.query("//device/cpu/name")[0].text.lower().replace("cm", "cortex-m")
    if device_file.query("//device/cpu/fpuPresent")[0].text in ("1", "true"):
        p["fpu"] = "fpv5-sp-d16"
    p["core"] = core
    p["revision"] = device_file.query("//device/cpu/revision")[0].text

    start, size = _MEMORIES[did.family]
    p["memories"] = [
        {"name": "ram", "access": "rwx", "size": str(size), "start": hex(start)},
        {"name": "core1", "access": "rwx", "size": str(0x1000), "start": hex(start + size)},
        {"name": "core0", "access": "rwx", "size": str(0x1000), "start": hex(start + size + 0x1000)},
    ]

    # Pin functions of modules without (an equally named) peripheral
    modules = [
        {"module": "jtag", "instance": "jtag"},
        {"module": "usb", "instance": "usb"},
        {"module": "xip", "instance": "xip"},
    ]
    if did.family == "23":
        modules += [
            {"module": "hstx", "instance": "hstx"},
            {"module": "coresight", "instance": "coresight"},
        ]
    gpios = []
    dma_channels = []
    clocks = []
    for m in device_file.query("//peripherals/peripheral"):
        modulename = m.find("name").text
        if modulename == "IO_BANK0":
            for r in m.findall("./registers/register"):
                if match := re.search(r"^GPIO(?P<name>\d+)_STATUS$", r.find("name").text):
                    name = match.group("name")
                    ctrl = m.find("./registers/register[name='GPIO" + name + "_CTRL']")
                    gpios.append({"name": name, "bank": "bank0", "idx": int(name), "funcs": _gpio_funcs(ctrl)})
        elif modulename == "IO_QSPI":
            # The index into the io[] array of the QSPI bank, the RP2350 has two
            # USB PHY pins before it, which are not GPIOs.
            idx = 0
            for r in m.findall("./registers/register"):
                if match := re.search(r"^GPIO_QSPI_(?P<name>.*)_STATUS$", r.find("name").text):
                    name = match.group("name").lower()
                    ctrl = m.find("./registers/register[name='GPIO_QSPI_" + name.upper() + "_CTRL']")
                    gpios.append({"name": name, "idx": idx, "bank": "qspi", "funcs": _gpio_funcs(ctrl)})
                    idx += 1
        elif modulename == "ADC":
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
    p["dma_channels"] = dma_channels
    p["clocks"] = clocks

    p["modules_map"] = {"clocks": {"module": "clocks", "instance": "clocks"}}
    for m in modules:
        p["modules_map"][m["instance"]] = m

    # Unique interrupts
    p["interrupts"] = []
    for i in device_file.query("//peripherals/peripheral/interrupt"):
        interrupt = {"position": i.find("value").text, "name": i.find("name").text}
        if interrupt not in p["interrupts"]:
            p["interrupts"].append(interrupt)
    return p
