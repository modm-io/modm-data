# Copyright 2016, Fabian Greif
# Copyright 2016, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import os
import re
import logging
from collections import defaultdict

from ..kg.stmicro import did_from_string
from ..kg import DeviceIdentifier
from ..utils import ext_path, XmlReader
from . import stm32_data
from ..cubehal import read_request_map as dmamux_request_map
from ..cubehal import read_bdma_request_map as dmamux_bdma_request_map
from ..cubehal.remaps import read_afio_remap_macros
from . import peripherals
from .dma_remap import dma_remaps
from .memories import memories as device_memories
from ..header2svd.stmicro import Header

LOGGER = logging.getLogger(__file__)


_EXT_PATH = ext_path("stmicro/")
_MCU_PATH = _EXT_PATH / "cubemx/mcu"
_FAMILY_FILE = None


def _family_file() -> XmlReader:
    global _FAMILY_FILE
    if _FAMILY_FILE is None:
        _FAMILY_FILE = XmlReader(_MCU_PATH / "families.xml")
    return _FAMILY_FILE


# ============================= MULTIPLE DEVICES ==============================
def _format_raw_devices(rawDevices):
    return list(sorted(set(d.get("RefName") for d in rawDevices)))


def devices_from_family(family: str) -> list[str]:
    """
    :param family: A STM32 family name, for example, F0, H7, G4.
    :return: A list of device names belonging to a STM32 family.
    """
    devices = _family_file().query(f'//Family[@Name="{family.upper()}"]/SubFamily/Mcu/@RefName')
    devices = _format_raw_devices(devices)
    LOGGER.info("Found devices of family '{}': {}".format(family, ", ".join(devices)))
    return devices


def devices_from_prefix(prefix: str) -> list[str]:
    """
    :param prefix: A STM32 device prefix, for example, STM32, STM32H7, STM32G432.
    :return: A list of device names starting with the prefix.
    """
    devices = _family_file().query(f'//Family/SubFamily/Mcu[starts-with(@RefName,"{prefix.upper()}")]')
    devices = _format_raw_devices(devices)
    devices = [d for d in devices if not stm32_data.ignoreDevice(d)]
    LOGGER.info("Found devices for prefix '{}': {}".format(prefix, ", ".join(devices)))
    return list(sorted(devices))


_TEMPERATURE_CODES = {0: "6", 85: "6", 105: "7", 125: "3"}


def temperature_codes(partname: str) -> list[str]:
    """
    CubeMX uses `x` as placeholder for the temperature range in the device name.
    This function computes the temperature codes from the maximum operating
    temperature, since every higher range also supports all the lower ranges.

    :param partname: A STM32 device name with temperature placeholder.
    :return: A sorted list of temperature codes, for example, `["3", "6", "7"]`.
    """
    codes = set()
    for device in _family_file().query(f'//Family/SubFamily/Mcu[@RefName="{partname}"]'):
        temp_max = device.find("Temperature")
        temp_max = "" if temp_max is None else temp_max.get("Max")
        temp_max = int(float(temp_max)) if len(temp_max) else min(_TEMPERATURE_CODES)
        codes.update(code for temp, code in _TEMPERATURE_CODES.items() if temp_max >= temp)
    return sorted(codes)


def cubemx_device_list() -> list[DeviceIdentifier]:
    """
    :return: A list of all STM32 device identifiers.
    """
    return [did_from_string(d) for d in devices_from_prefix("STM32")]


# ============================= INDIVIDUAL DEVICE =============================
def devices_from_partname(partname: str) -> list[dict[str]]:
    """
    Find the STM32 device name in the STM32CubeMX database and convert the data
    into a device specific key-value mapping describing the hardware.

    .. note::
       One partname may contain multiple devices, for example, multiple
       asymmetric CPU cores like in the STM32H745 device.

    :param partname: A full STM32 device name.
    :return: a list of dictionaries containing a device specific data structure.
    """
    deviceNames = _family_file().query(f'//Family/SubFamily/Mcu[starts-with(@RefName,"{partname}")]')
    comboDeviceName = sorted([d.get("Name") for d in deviceNames])[0]
    device_file = XmlReader(os.path.join(_MCU_PATH, comboDeviceName + ".xml"))
    did = did_from_string(partname.lower())
    LOGGER.info(f"Parsing '{did.string}'")

    # information about the core and architecture
    cores = [c.text.lower().replace("arm ", "").replace("+", "plus") for c in device_file.query("//Core")]
    if len(cores) > 1:
        did.naming_schema += "@{core}"
    devices = [_properties_from_id(partname, comboDeviceName, device_file, did.copy(), c) for c in cores]
    return [d for d in devices if d is not None]


def _properties_from_id(partname, comboDeviceName, device_file, did, core):
    if "@" in did.naming_schema:
        did.set("core", core[7:9])
    p = {"id": did, "die": device_file.query("//Die/text()")[0]}

    dfp_folder = "STM32{}xx_DFP".format(did.family.upper())
    if did.string[5:8] in ["h7r", "h7s"]:
        dfp_folder = "STM32H7RSxx_DFP"
    elif did.string[5:8] == "wb0":
        dfp_folder = "STM32WB0x_DFP"
    elif did.string[5:8] == "wba":
        dfp_folder = "STM32WBAxx_DFP"
    elif did.string[5:8] == "wl3":
        dfp_folder = "STM32WL3x_DFP"

    # Find OpenCMSIS pack file
    dfp_file = XmlReader(os.path.join(_EXT_PATH, dfp_folder, f"Keil.{dfp_folder}.pdsc"))

    # Find the correct DFP start node
    dfp_node = dfp_file.query(f'//variant[starts-with(@Dvariant,"{partname}")]')
    if not dfp_node:
        dfp_node = dfp_file.query(f'//device[starts-with(@Dname,"{partname}")]')
        if not dfp_node:
            dfp_node = dfp_file.query(f'//device[starts-with(@Dname,"{partname[:11]}")]')

    if not dfp_node:
        LOGGER.error(f"No DFP device entry found for {did.string}: {partname}")
        return None

    def dfp_findall(key, attribs=None):
        values = []
        node = dfp_node[0]
        while node.tag != "devices":
            values += node.findall(key)
            node = node.getparent()
        if core := did.get("core"):
            values = [v for v in values if core.upper() in v.get("Pname", core.upper())]
        if attribs is not None:
            # The attributes of the inner nodes take precedence over the outer nodes
            values = {a: v.get(a) for v in reversed(values) for a in attribs if v.get(a)}
        return values

    # Find the correct CMSIS header
    dfp_compile = dfp_findall("compile")[0].get("define")
    p["cmsis_header"] = cmsis_header = dfp_folder[:-4].lower()
    stm_header = Header(did, cmsis_header, dfp_compile)
    if not stm_header.is_valid:
        LOGGER.error("CMSIS Header invalid for %s", did.string)
        return None
    p["define"] = stm_header.define
    p["interrupts"] = stm_header.interrupt_table

    # Find out about the CPU
    p["core"] = core
    processor = dfp_findall("processor", ["DcoreVersion", "Dclock", "Dfpu"])
    if (fpu := processor.get("Dfpu")) in ("1", "SP_FPU"):
        p["fpu"] = "fpv4-sp-d16" if "m4" in core else "fpv5-sp-d16"
    elif fpu == "DP_FPU":
        p["fpu"] = "fpv5-d16"
    if rev := processor.get("DcoreVersion"):
        p["revision"] = rev.lower()

    # Maximum operating frequency, which is too low in the DFP or CubeMX data of some devices,
    # for example, the DFP lists STM32G441/G473/G484 with 150 MHz and STM32L4P5/Q5 with 80 MHz,
    # and both list some STM32H7R/S packages with 550 MHz. Only the DFP contains the maximum
    # frequency of each core of multi-core devices.
    frequencies = [int(float(f.text) * 1e6) for f in device_file.query("//Frequency")]
    if max_frequency := processor.get("Dclock"):
        max_frequency = int(max_frequency)
        if not did.get("core"):
            max_frequency = max([max_frequency] + frequencies)
    elif frequencies:
        LOGGER.warning(f"Fallback to //Frequency for max frequency for {did.string}!")
        max_frequency = frequencies[0]
    p["max_frequency"] = max_frequency

    # Find all internal memories
    memories = {
        m.get("name", m.get("id")).lower(): {
            "access": m.get("access", "rwx"),
            "start": int(m.get("start"), 0),
            "size": int(m.get("size"), 0),
            "alias": m.get("alias", "").lower(),
        }
        for m in (dfp_findall("memory") + dfp_findall("algorithm"))
        if "gfx" not in m.get("name", m.get("id")).lower()
    }
    p["memories"] = device_memories(did, memories, stm_header, p["die"])

    # packaging
    package = device_file.query("//@Package")[0]
    p["pin-count"] = re.findall(r"[0-9]+", package)[0]
    p["package"] = re.findall(r"[A-Za-z\.]+", package)[0]

    def clean_up_version(version):
        match = re.search("v[1-9]_[0-9x]", version.replace(".", "_"))
        if match:
            version = match.group(0).replace("_", ".")
        else:
            pass
            # print(version)
        return version

    modules = []
    dmaFile = None
    bdmaFile = None
    hasFlashModule = False
    for ip in device_file.query("//IP"):
        # These IPs are all software modules, NOT hardware modules. Their version string is weird too.
        software_ips = {
            "GFXSIMULATOR",
            "GRAPHICS",
            "FATFS",
            "TOUCHSENSING",
            "PDM2PCM",
            "MBEDTLS",
            "FREERTOS",
            "CORTEX_M",
            "NVIC",
            "USB_DEVICE",
            "USB_HOST",
            "LWIP",
            "LIBJPEG",
            "GUI_INTERFACE",
            "TRACER",
            "FILEX",
            "LEVELX",
            "THREADX",
            "USBX",
            "LINKEDLIST",
            "NETXDUO",
            "BOOTPATH",
            "MEMORYMAP",
            "OPENAMP",
            "USB_OTG_FS1",
            "USB_OTG_HS1",
            "ADV_TRACE",
            "COMMON_BLE",
            "COMMON_WPAN",
            "KMS",
            "LORAWAN",
            "MISC",
            "SEQUENCER",
            "SIGFOX",
            "STM32_BLE",
            "STM32_WPAN",
            "SUBGHZ_PHY",
            "TIMER",
            "TINY_LPM",
            "WMBUS",
        }
        if any(ip.get("Name").upper().startswith(p) for p in software_ips):
            continue
        # Some STM32U5 files list the CRC a second time with a CRS instance
        if ip.get("Name") == "CRC" and ip.get("InstanceName") != "CRC":
            continue

        rversion = ip.get("Version")
        module = (ip.get("Name"), ip.get("InstanceName"), clean_up_version(rversion))
        if "flash" in module[0].lower():
            hasFlashModule = True

        if module[0] == "DMA":
            # lets load additional information about the DMA
            dmaFile = XmlReader(os.path.join(_MCU_PATH, "IP", "DMA-" + rversion + "_Modes.xml"))
            for rdma in dmaFile.query('//IP/ModeLogicOperator/Mode[starts-with(@Name,"DMA")]/@Name'):
                for dma in rdma.split(","):
                    modules.append((module[0].lower(), dma.strip().lower(), module[2].lower()))
            continue
        elif module[0].startswith("BDMA"):
            module = ("BDMA",) + module[1:]
            # Ignore BDMA1 data
            # If two instances exist the first one is hard-wired to DFSDM channels and has no associated DMAMUX data
            if module[1] != "BDMA1":
                bdmaFile = XmlReader(os.path.join(_MCU_PATH, "IP", module[1] + "-" + rversion + "_Modes.xml"))
        elif module[0].startswith("TIM"):
            module = ("TIM",) + module[1:]
        elif module[0] == "MDF" and module[1].startswith("ADF"):
            module = ("ADF",) + module[1:]
        elif module[0] == "USB_DRD_FS":
            module = (
                "USB",
                "USB",
            ) + module[2:]
        elif module[0] == "SPDIFRX":
            # Only one instance exists, which is named without instance in headers and manuals
            module = ("SPDIFRX", "SPDIFRX") + module[2:]

        modules.append(tuple([m.lower() for m in module]))

    if not hasFlashModule:
        modules.append(("flash", "flash", "v1.0"))
    modules = [m + peripherals.getPeripheralData(did, m) for m in modules]

    p["modules"] = modules
    LOGGER.debug("Available Modules are:\n" + _modulesToString(modules))
    # print("\n".join(str(m) for m in modules))

    # Flash latency table
    p["flash_latency"] = stm32_data.getFlashLatencyForDevice(did)

    # lets load additional information about the GPIO IP
    ip_file = device_file.query('//IP[@Name="GPIO"]')[0].get("Version")
    ip_file = os.path.join(_MCU_PATH, "IP", "GPIO-" + ip_file + "_Modes.xml")
    gpioFile = XmlReader(ip_file)

    pins = device_file.query('//Pin[@Type="I/O"][starts-with(@Name,"P")]')

    def raw_pin_sort(p):
        port = p.get("Name")[1:2]
        pin = p.get("Name")[:4]
        if len(pin) > 3 and not pin[3].isdigit():
            pin = pin[:3]
        return (port, int(pin[2:]))

    pins = sorted(pins, key=raw_pin_sort)
    # Remove package remaps from GPIO data (but not from package)
    pins.sort(key=lambda p: "PINREMAP" not in p.get("Variant", ""))

    def pin_name(name):
        name = name[:4]
        if len(name) > 3 and not name[3].isdigit():
            name = name[:3]
        return (name[1:2].lower(), name[2:].lower())

    # Find the remap pin pairs, if they exist
    double_pinouts = defaultdict(list)
    for pin in device_file.query("//Pin"):
        double_pinouts[pin.get("Position")].append((pin.get("Name"), pin.get("Variant", "DEFAULT")))
    double_pinouts = {
        pos: {pin: variant for (pin, variant) in pins}
        for pos, pins in double_pinouts.items()
        if len(pins) > 1 and any("PINREMAP" in pin[1] for pin in pins)
    }

    # Get the pinout for this package with correct remap variants
    pinout = []
    for pin in device_file.query("//Pin"):
        name = pin.get("Name")
        pos = pin.get("Position")
        pinv = {
            "name": name,
            "position": pos,
            "type": pin.get("Type"),
        }
        variant = double_pinouts.get(pos, {}).get(name)
        if variant is not None and (
            pin.get("Type") != "I/O" or (pin_name(name)[0] in ["a"] and pin_name(name)[1] in ["9", "10", "11", "12"])
        ):
            pinv["variant"] = "remap" if "PINREMAP" in variant else "remap-default"
        pinout.append(pinv)

    p["pinout"] = pinout
    p["package"] = device_file.query("/Mcu/@Package")[0]

    if dmaFile is not None:
        dma_dumped = []
        dma_streams = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        dma_request_map = None
        dma_remap = dma_remaps(did, dmaFile, stm_header)
        for sig in dmaFile.query('//ModeLogicOperator[@Name="XOR"]/Mode'):
            name = rname = sig.get("Name")
            if did.family == "wl" and rname in ["SAI1_A", "SAI1_B", "QUADSPI"]:
                continue  # CubeMX data is wrong, WL devices don't have these peripherals

            parent = sig.getparent().getparent().get("Name")
            dma_signal_remaps = dma_remap.get((parent, rname), [])
            instance = parent.split("_")[0][3:]
            parent = parent.split("_")[1]

            request = dmaFile.query(f'//RefMode[@Name="{name}"]')[0]

            def rv(param, default=[]):
                vls = request.xpath(f'./Parameter[@Name="{param}"]/PossibleValue/text()')
                if not len(vls):
                    vls = default
                return vls

            name = name.lower().split(":")[0]
            if name == "memtomem":
                continue

            # Several corrections
            name = name.replace("spdif_rx", "spdifrx")
            if name.startswith("dac") and "_" not in name:
                name = f"dac_{name}"
            if any(name == n for n in ["sdio", "sdmmc2", "sdmmc1"]):
                continue
            if len(name.split("_")) < 2:
                name = f"{name}_default"
            driver, inst, name = split_dma_signal(name, modules)

            if "[" in parent:
                if dma_request_map is None:
                    dma_request_map = dmamux_request_map(did)
                channel = dma_request_map[rname]
                stream = instance = 0
                p["dma_naming"] = (None, "request", "signal")
            elif "Stream" in parent:
                channel = rv("Channel", ["software"])[0].replace("DMA_CHANNEL_", "")
                stream = parent.replace("Stream", "")
                p["dma_naming"] = ("stream", "channel", "signal")
            elif rv("Request"):
                channel = rv("Request", ["software"])[0].replace("DMA_REQUEST_", "")
                stream = parent.replace("Channel", "")
                p["dma_naming"] = ("channel", "request", "signal")
            else:
                channel = parent.replace("Channel", "")
                stream = channel
                p["dma_naming"] = (None, "channel", "signal")

            if driver is None:  # peripheral is not part of this device
                dma_dumped.append((instance, stream, name))
                continue
            mode = [v[4:].lower() for v in rv("Mode")]
            for sname in [None] if name == "default" else name.split("/"):
                signal = {
                    "driver": driver,
                    "name": sname,
                    "direction": [
                        v[4:].replace("PERIPH", "p").replace("MEMORY", "m").replace("_TO_", "2")
                        for v in rv("Direction")
                    ],
                    "mode": mode,
                    "increase": "ENABLE" in rv("PeriphInc", ["DMA_PINC_ENABLE"])[0],
                }
                if inst:
                    signal["instance"] = inst
                if dma_signal_remaps:
                    signal["remap"] = [{"position": p, "mask": m, "id": v} for p, m, v in dma_signal_remaps]
                dma_streams[instance][stream][channel].append(signal)

        # The channels without remap require the remap fields of the same signal on other channels to be reset
        remap_fields = defaultdict(set)
        dma_signals = [s for ss in dma_streams.values() for chs in ss.values() for c in chs.values() for s in c]
        for signal in dma_signals:
            key = (signal["driver"], signal.get("instance"), signal["name"])
            remap_fields[key].update((r["position"], r["mask"]) for r in signal.get("remap", []))
        for signal in dma_signals:
            key = (signal["driver"], signal.get("instance"), signal["name"])
            positions = {r["position"] for r in signal.get("remap", [])}
            defaults = [{"position": p, "mask": m, "id": 0} for p, m in remap_fields[key] if p not in positions]
            if remap := sorted(signal.get("remap", []) + defaults, key=lambda r: r["position"]):
                signal["remap"] = remap

        # Manually handle condition expressions from XML for
        # (STM32F030CCTx|STM32F030RCTx) and (STM32F070CBTx|STM32F070RBTx)
        if did.family in ["f0"]:
            if did.name == "30" and did.size == "c":
                dma_streams["1"].pop("6")
                dma_streams["1"].pop("7")
                dma_streams.pop("2")
            if did.name == "70" and did.size == "b":
                dma_streams["1"].pop("6")
                dma_streams["1"].pop("7")

        # De-duplicate DMA signal entries
        def deduplicate_list(dl):
            return [i for n, i in enumerate(dl) if i not in dl[n + 1 :]]

        for stream in dma_streams:
            for channel in dma_streams[stream]:
                for signal in dma_streams[stream][channel]:
                    dma_streams[stream][channel][signal] = deduplicate_list(dma_streams[stream][channel][signal])

        # if p["dma_naming"][1] == "request":
        #     print(did, dmaFile.filename)
        p["dma"] = dma_streams
        if len(dma_dumped):
            for instance, stream, name in sorted(dma_dumped):
                LOGGER.debug(f"DMA{instance}#{stream}: dumping {name}")

        # If DMAMUX is used, add DMAMUX to DMA peripheral channel mappings
        if p["dma_naming"] == (None, "request", "signal"):
            # There can be multiple "//RefParameter[@Name="Instance"]" nodes constrained by
            # a <Condition> child node filtering by the STM32 die id
            # Try to match a node with condition first, if nothing matches choose the default one
            die_id = device_file.query("//Die")[0].text
            q = f'//RefParameter[@Name="Instance"]/Condition[@Expression="{die_id}"]/../PossibleValue/@Value'
            channels = dmaFile.query(q)
            if len(channels) == 0:
                # match channels from node without <Condition> child node
                channels = dmaFile.query('//RefParameter[@Name="Instance" and not(Condition)]/PossibleValue/@Value')

            mux_channels = []
            # H7 has "Stream" instead of "Channel" for DMAMUX1
            mux_channel_regex = re.compile(r"DMA(?P<instance>([0-9]))_(Channel|Stream)(?P<channel>([0-9]+))")
            for mux_ch_position, channel in enumerate(channels):
                m = mux_channel_regex.match(channel)
                assert m is not None
                mux_channels.append(
                    {
                        "position": mux_ch_position,
                        "dma-instance": int(m.group("instance")),
                        "dma-channel": int(m.group("channel")),
                    }
                )
            p["dma_mux_channels"] = mux_channels

    if bdmaFile is not None:
        bdma_channels = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        p["bdma_naming"] = (None, "request", "signal")
        bdma_request_map = None
        for sig in bdmaFile.query('//ModeLogicOperator[@Name="XOR"]/Mode'):
            name = rname = sig.get("Name")

            request = bdmaFile.query('//RefMode[@Name="{}"]'.format(name))[0]
            name = name.lower().split(":")[0]
            if name == "memtomem":
                continue

            if name.startswith("dac") and "_" not in name:
                name = "dac_{}".format(name)
            if len(name.split("_")) < 2:
                name = "{}_default".format(name)
            driver, inst, name = split_dma_signal(name, modules)

            if bdma_request_map is None:
                bdma_request_map = dmamux_bdma_request_map(did)
            channel = bdma_request_map[rname]

            if driver is None:  # peripheral is not part of this device
                continue

            mode = [v[4:].lower() for v in rv("Mode")]
            for sname in [None] if name == "default" else name.split("/"):
                signal = {
                    "driver": driver,
                    "name": sname,
                    "direction": [
                        v[4:].replace("PERIPH", "p").replace("MEMORY", "m").replace("_TO_", "2")
                        for v in rv("Direction")
                    ],
                    "mode": mode,
                    "increase": "ENABLE" in rv("PeriphInc", ["DMA_PINC_ENABLE"])[0],
                }
                if inst:
                    signal["instance"] = inst
                bdma_channels[0][0][channel].append(signal)
                # print(channel)
                # print(signal)

        p["bdma"] = bdma_channels

        mux_channels = []
        mux_channel_regex = re.compile(r"BDMA(?P<instance>2)?_Channel(?P<channel>([0-9]+))")
        channel_names = bdmaFile.query('//RefParameter[@Name="Instance" and not(Condition)]/PossibleValue/@Value')
        for mux_ch_position, channel in enumerate(channel_names):
            m = mux_channel_regex.match(channel)
            assert m is not None
            if m.group("instance"):
                mux_channels.append(
                    {
                        "position": mux_ch_position,
                        "dma-instance": int(m.group("instance")),
                        "dma-channel": int(m.group("channel")),
                    }
                )
            else:
                mux_channels.append({"position": mux_ch_position, "dma-channel": int(m.group("channel"))})
        p["bdma_mux_channels"] = mux_channels

    if did.family == "f1":
        grouped_f1_signals = gpioFile.compactQuery("//GPIO_Pin/PinSignal/@Name")

    _seen_gpio = set()
    gpios = []
    signals = set()
    for pin in pins:
        rname = pin.get("Name")
        name = pin_name(rname)

        # the analog channels are only available in the Mcu file, not the GPIO file
        localSignals = device_file.compactQuery(f'//Pin[@Name="{rname}"]/Signal[not(@Name="GPIO")]/@Name')
        # print(name, localSignals)
        altFunctions = []

        if did.family == "f1":
            altFunctions = [(s.lower(), "-1") for s in localSignals if s not in grouped_f1_signals]
        else:
            signalMap = {}
            # Pins with remap or special function names, like "PA9 [PA11]" or "PB7-BOOT0 (PB7)",
            # may not have any AF data in the GPIO file, so fall back to the plain pin name.
            for gpio_name in dict.fromkeys((re.match(r"P[A-Z]\d+", rname).group(0), rname)):
                allSignals = gpioFile.compactQuery(
                    f'//GPIO_Pin[@Name="{gpio_name}"]/PinSignal/SpecificParameter[@Name="GPIO_AF"]/..'
                )
                signalMap |= {
                    a.get("Name"): a[0][0].text.lower().replace("gpio_af", "")[:2].replace("_", "") for a in allSignals
                }

            def signal_af(signal):
                # The GPIO file sometimes uses different signal names than the MCU file
                aliases = (
                    signal,
                    signal.removeprefix("USB_"),
                    signal.removeprefix("SYS_"),
                    signal.replace("ETH_", "ETH_MII_"),
                    signal + "_OUT",
                )
                return next((signalMap[a] for a in aliases if a in signalMap), "-1")

            altFunctions = [(s.lower(), signal_af(s)) for s in localSignals]

        afs = []
        for af in altFunctions:
            for raf in split_signals(af[0], modules):
                # Some pins still list signals of peripherals that are not part of this device
                if raf[0] is None:
                    LOGGER.debug(f"Ignoring signal without peripheral: {rname} {af[0]}")
                    continue
                naf = {}
                naf["driver"], naf["instance"], naf["name"] = raf
                naf["af"] = af[1] if int(af[1]) >= 0 else None
                afs.append(naf)
                signals.add(naf["name"])

        gpio = (name[0], name[1], afs)
        if name not in _seen_gpio:
            gpios.append(gpio)
            _seen_gpio.add(name)
        # LOGGER.debug(f"{gpio[0].upper()}{gpio[1]}: {afs}")

    remaps = {}
    if did.family == "f1":
        # The remap blocks reference the HAL macro, which uses the CMSIS defines of the remap bits
        afio_macros = read_afio_remap_macros()
        blocks = {}
        for block in gpioFile.query("//GPIO_Pin/PinSignal/RemapBlock"):
            macro = block.xpath("./SpecificParameter/PossibleValue/text()")
            blocks.setdefault(block.get("Name"), macro[0] if macro else None)
        fields, values = {}, {}
        for remap, macro in blocks.items():
            module = remap.split("_")[0].lower()
            values[remap] = 0  # The default remap uses the reset value
            if macro is None:
                continue
            register, value, mask = afio_macros[macro]
            if (mask := stm_header.define_value(mask)) is None:
                LOGGER.debug(f"AFIO remap {remap} is not available for {did.string}")
                continue
            position = (mask & -mask).bit_length() - 1
            fields[module] = (position + (32 if register == "MAPR2" else 0), mask >> position)
            values[remap] = (stm_header.define_value(value) >> position) if value else 0

        for remap in blocks:
            module = remap.split("_")[0].lower()
            if module not in fields:
                continue
            config = remap.split("_")[1]

            mpins = []
            for pin in gpioFile.compactQuery(f'//GPIO_Pin/PinSignal/RemapBlock[@Name="{remap}"]/..'):
                name = pin.getparent().get("Name")[:4].split("-")[0].split("/")[0].strip().lower()
                pport, ppin = name[1:2], name[2:]
                if not any([pp[0] == pport and pp[1] == ppin for pp in gpios]):
                    continue
                mmm = {"port": pport, "pin": ppin}
                driver, _, name = split_signal(pin.get("Name").lower(), modules)
                if driver is None:
                    continue
                mmm["name"] = name
                signals.add(name)
                mpins.append(mmm)

            if module not in remaps:
                driver, instance, _ = split_signal(module + "_lol", modules)
                if not driver:
                    continue
                remaps[module] = {
                    "position": fields[module][0],
                    "mask": fields[module][1],
                    "groups": {},
                    "driver": driver,
                    "instance": instance,
                }
            if len(mpins) > 0:
                remaps[module]["groups"][values[remap]] = mpins
                LOGGER.debug(
                    "{:<20}{}".format(module + "_" + config, [f"{b['port']}{b['pin']}:{b['name']}" for b in mpins])
                )

    p["remaps"] = remaps
    p["gpios"] = gpios
    p["signals"] = signals

    return p


def split_signal(signal: str, modules: list[tuple]) -> tuple[str, str, str]:
    """
    Splits a lower case CubeMX signal name into driver, instance, and name using
    the names and instance names of the device modules.

    :return: A tuple of (driver, instance, name), driver and instance may be None.
    """
    # entry 0 contains names without instance
    # entry 1 contains names with instance
    minst = [m for m in modules if signal.startswith(m[1] + "_")]
    mdriv = [m for m in modules if signal.startswith(m[0] + "_")]
    if len(minst) > 1:
        LOGGER.warning(f"Ambiguous driver: {signal} {minst}")
        exit(1)

    if minst:
        driver, prefix = minst[0][:2]
        instance = prefix.replace(driver, "")
    elif mdriv:
        driver = prefix = mdriv[0][0]
        instance = None
    else:
        LOGGER.debug(f"Unknown driver: {signal}")
        return (None, None, signal)

    name = signal[len(prefix) + 1 :]
    if not len(name):
        LOGGER.error(f"Unknown name: {signal} {minst}")
        exit(1)
    return (driver, instance or None, name)


def split_dma_signal(signal: str, modules: list[tuple]) -> tuple[str, str, str]:
    """
    Splits a DMA request name like `split_signal()`. DMA requests often omit the
    instance of peripherals that only have one instance, for example, `lpuart_tx`,
    so the instance is taken from the device modules. The requests of the DMA
    controller itself, for example, `dma_generator0`, are not assigned to a DMA
    instance, since they belong to the DMA multiplexer.

    :return: A tuple of (driver, instance, name), driver and instance may be None.
    """
    driver, instance, name = split_signal(signal, modules)
    if driver is not None and instance is None and driver not in ("dma", "bdma"):
        instances = {m[1].replace(driver, "") for m in modules if m[0] == driver}
        if len(instances) == 1:
            instance = instances.pop() or None
    return (driver, instance, name)


# CubeMX signal names that are not prefixed with the name of their peripheral module.
# The peripheral is prepended so that the signal names stay close to the raw names.
_SIGNAL_PERIPHERALS = (
    (re.compile(r"cec"), r"hdmi_cec_cec"),
    (re.compile(r"ir_(.+)"), r"irtim_\1"),
    (re.compile(r"timx_(.+)"), r"tim_\1"),
    (re.compile(r"spdifrx1_(.+)"), r"spdifrx_\1"),
    (re.compile(r"crs1?_sync|i2s_ckin|audioclk"), r"rcc_\g<0>"),
    (re.compile(r"boot0"), r"boot_\g<0>"),
    (re.compile(r"vddtcxo"), r"subghz_\g<0>"),
)
# Pins shared by JTAG and SWD list both signals, for example, `sys_jtms-swdio`
_JTAG_SWD_SIGNALS = re.compile(r"(jt[a-z]+)-([a-z]*sw[a-z]+)")


def split_signals(signal: str, modules: list[tuple]) -> list[tuple[str, str, str]]:
    """
    Splits a lower case CubeMX signal name that may contain multiple signals
    separated by `-`, for example, `sys_jtms-swdio`. Other names containing `-`
    describe a single signal, for example, `debug_rf-busy` becomes `rf_busy`.

    :return: A list of (driver, instance, name) tuples.
    """
    for pattern, replacement in _SIGNAL_PERIPHERALS:
        if pattern.fullmatch(signal):
            signal = pattern.sub(replacement, signal)
            break

    driver, instance, name = split_signal(signal, modules)
    if match := _JTAG_SWD_SIGNALS.fullmatch(name):
        return [(driver, instance, n) for n in match.groups()]
    return [(driver, instance, name.replace("-", "_"))]


def _modulesToString(modules):
    string = ""
    mods = sorted(modules)
    char = mods[0][0][0:1]
    for _, instance, _, _, _, _ in mods:
        if not instance.startswith(char):
            string += "\n"
        string += instance + " \t"
        char = instance[0][0:1]
    return string
