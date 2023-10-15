# Copyright 2016, Fabian Greif
# Copyright 2016, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import os
import re
import logging
from collections import defaultdict

from ..owl.stmicro import did_from_string
from ..utils import ext_path, XmlReader
from . import stm32_data
from ..cubehal import read_request_map as dmamux_request_map
from . import peripherals

LOGGER = logging.getLogger(__file__)


_MCU_PATH = ext_path("stmicro/cubemx/mcu")
_FAMILY_FILE = None

def _family_file() -> XmlReader:
    global _FAMILY_FILE
    if _FAMILY_FILE is None:
        _FAMILY_FILE = XmlReader(_MCU_PATH / "families.xml")
    return _FAMILY_FILE


# ============================= MULTIPLE DEVICES ==============================
def _format_raw_devices(rawDevices):
    TemperatureMap = {0: "6", 105: "7", 125: "3"}
    devices = set()
    for dev in rawDevices:
        temp_max = dev.find("Temperature")
        temp_max = "" if temp_max is None else temp_max.get("Max")
        name = dev.get("RefName")
        temp_max = int(float(temp_max)) if len(temp_max) else min(TemperatureMap)
        for temp, value in TemperatureMap.items():
            if temp_max >= temp:
                devices.add(name[:12] + value + name[13:])
    return sorted(list(devices))


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

def cubemx_device_list() -> list["modm_data.owl.DeviceIdentifier"]:
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
    deviceNames = _family_file().query('//Family/SubFamily/Mcu[starts-with(@RefName,"{}")]'
                                                 .format(partname[:12] + "x" + partname[13:]))
    comboDeviceName = sorted([d.get("Name") for d in deviceNames])[0]
    device_file = XmlReader(os.path.join(_MCU_PATH, comboDeviceName + ".xml"))
    did = did_from_string(partname.lower())
    LOGGER.info("Parsing '{}'".format(did.string))

    # information about the core and architecture
    cores = [c.text.lower().replace("arm ", "") for c in device_file.query('//Core')]
    if len(cores) > 1: did.naming_schema += "@{core}"
    devices = [_properties_from_id(comboDeviceName, device_file, did.copy(), c) for c in cores]
    return [d for d in devices if d is not None]


def _properties_from_id(comboDeviceName, device_file, did, core):
    if core.endswith("m4") or core.endswith("m7") or core.endswith("m33"):
        core += "f"
    if did.family in ["h7"] or (did.family in ["f7"] and did.name not in ["45", "46", "56"]):
        core += "d"
    if "@" in did.naming_schema:
        did.set("core", core[7:9])
    p = {"id": did, "core": core}

    # Maximum operating frequency
    max_frequency = float(device_file.query('//Frequency')[0].text)
    # H7 dual-core devices run the M4 core at half the frequency as the M7 core
    if did.get("core", "") == "m4": max_frequency /= 2.0;
    p["max_frequency"] = int(max_frequency * 1e6)

    # Information from the CMSIS headers
    # stm_header = Header(did)
    # if not stm_header.is_valid:
    #     LOGGER.error("CMSIS Header invalid for %s", did.string)
    #     return None

    # flash and ram sizes
    # The <ram> and <flash> can occur multiple times.
    # they are "ordered" in the same way as the `(S-I-Z-E)` ids in the device combo name
    # we must first find out which index the current did.size has inside `(S-I-Z-E)`
    sizeIndexFlash = 0
    sizeIndexRam = 0

    match = re.search(r"\(.(-.)*\)", comboDeviceName)
    if match:
        sizeArray = match.group(0)[1:-1].lower().split("-")
        sizeIndexFlash = sizeArray.index(did.size)
        sizeIndexRam = sizeIndexFlash

    rams = sorted([int(r.text) for r in device_file.query('//Ram')])
    if sizeIndexRam >= len(rams):
        sizeIndexRam = len(rams) - 1

    flashs = sorted([int(f.text) for f in device_file.query('//Flash')])
    if sizeIndexFlash >= len(flashs):
        sizeIndexFlash = len(flashs) - 1


    p["ram"] = rams[sizeIndexRam] * 1024
    p["flash"] = flashs[sizeIndexFlash] * 1024

    memories = []
    for (mem_name, mem_start, mem_size) in stm32_data.getMemoryForDevice(did, p["flash"], p["ram"]):
        access = "rwx"
        if did.family == "f4" and mem_name == "ccm": access = "rw";
        if "flash" in mem_name: access = "rx";
        memories.append({"name": mem_name, "access": access, "size": str(mem_size),
                         "start": "0x{:02X}".format(mem_start)})

    p["memories"] = memories

    # packaging
    package = device_file.query('//@Package')[0]
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
    for ip in device_file.query('//IP'):
        # These IPs are all software modules, NOT hardware modules. Their version string is weird too.
        software_ips = {"GFXSIMULATOR", "GRAPHICS", "FATFS", "TOUCHSENSING", "PDM2PCM",
                        "MBEDTLS", "FREERTOS", "CORTEX_M", "NVIC", "USB_DEVICE",
                        "USB_HOST", "LWIP", "LIBJPEG", "GUI_INTERFACE", "TRACER",
                        "FILEX", "LEVELX", "THREADX", "USBX", "LINKEDLIST", "NETXDUO"}
        if any(ip.get("Name").upper().startswith(p) for p in software_ips):
            continue

        rversion = ip.get("Version")
        module = (ip.get("Name"), ip.get("InstanceName"), clean_up_version(rversion))

        if module[0] == "DMA":
            # lets load additional information about the DMA
            dmaFile = XmlReader(os.path.join(_MCU_PATH, "IP", "DMA-" + rversion + "_Modes.xml"))
            for rdma in dmaFile.query('//IP/ModeLogicOperator/Mode[starts-with(@Name,"DMA")]/@Name'):
                for dma in rdma.split(","):
                    modules.append((module[0].lower(), dma.strip().lower(), module[2].lower()))
            continue
        if module[0].startswith("TIM"):
            module = ("TIM",) + module[1:]

        modules.append(tuple([m.lower() for m in module]))

    modules.append( ("flash", "flash", "v1.0"))
    modules = [m + peripherals.getPeripheralData(did, m) for m in modules]

    p["modules"] = modules
    LOGGER.debug("Available Modules are:\n" + _modulesToString(modules))
    instances = [m[1] for m in modules]
    # print("\n".join(str(m) for m in modules))

    # p["stm_header"] = stm_header
    # p["interrupts"] = stm_header.interrupt_table
    # Flash latency table
    # p["flash_latency"] = stm32_data.getFlashLatencyForDevice(did)

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

    gpios = []

    def pin_name(name):
        name = name[:4]
        if len(name) > 3 and not name[3].isdigit():
            name = name[:3]
        return (name[1:2].lower(), name[2:].lower())

    # Find the remap pin pairs, if they exist
    double_pinouts = defaultdict(list)
    for pin in device_file.query('//Pin'):
        double_pinouts[pin.get("Position")].append((pin.get("Name"), pin.get("Variant", "DEFAULT")))
    double_pinouts = {pos: {pin:variant for (pin, variant) in pins}
                            for pos, pins in double_pinouts.items()
                                if len(pins) > 1 and any("PINREMAP" in pin[1] for pin in pins)}

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
        if (variant is not None and (pin.get("Type") != "I/O" or (
            pin_name(name)[0] in ['a'] and
            pin_name(name)[1] in ['9', '10', '11', '12']))):
            pinv["variant"] = "remap" if "PINREMAP" in variant else "remap-default"
        pinout.append(pinv)

    p["pinout"] = pinout
    p["package"] = device_file.query("/Mcu/@Package")[0]

    def split_af(af):
        # entry 0 contains names without instance
        # entry 1 contains names with instance
        mdriv = [m for m in modules if af.startswith(m[0] + "_")]
        minst = [m for m in modules if af.startswith(m[1] + "_")]
        # print(af, mdriv, minst)
        if len(minst) > 1:
            LOGGER.warning("Ambiguos driver: {} {}".format(af, minst))
            exit(1)

        minst = minst[0] if len(minst) else None
        mdriv = mdriv[0] if len(mdriv) else None

        driver = minst[0] if minst else (mdriv[0] if mdriv else None)
        if not driver:
            LOGGER.debug("Unknown driver: {}".format(af))
        instance = None
        if minst and driver:
            pinst = minst[1].replace(driver, "")
            if len(pinst): instance = pinst;
        if minst or mdriv:
            name = af.replace((minst[1] if minst else mdriv[0]) + "_", "")
            if not len(name):
                LOGGER.error("Unknown name: {} {}".format(af, minst, mdriv))
                exit(1)
        else:
            name = af

        return (driver, instance, name)

    def split_multi_af(af):
        af = af.replace("ir_", "irtim_") \
               .replace("crs_", "rcc_crs_") \
               .replace("timx_", "tim_")
        if af == "cec": af = "hdmi_cec_cec";

        driver, instance, names = split_af(af)
        rafs = []
        for name in names.split("-"):
            rafs.append( (driver, instance, name) )
        return rafs

    if dmaFile is not None:
        dma_dumped = []
        dma_streams = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        dma_request_map = None
        for sig in dmaFile.query('//ModeLogicOperator[@Name="XOR"]/Mode'):
            name = rname = sig.get("Name")
            if did.family == "wl" and rname in ["SAI1_A", "SAI1_B", "QUADSPI"]:
                continue  # CubeMX data is wrong, WL devices don't have these peripherals

            parent = sig.getparent().getparent().get("Name")
            instance = parent.split("_")[0][3:]
            parent = parent.split("_")[1]

            request = dmaFile.query('//RefMode[@Name="{}"]'.format(name))[0]
            def rv(param, default=[]):
                vls = request.xpath('./Parameter[@Name="{}"]/PossibleValue/text()'.format(param))
                if not len(vls): vls = default;
                return vls

            name = name.lower().split(":")[0]
            if name == "memtomem":
                continue

            # Several corrections
            name = name.replace("spdif_rx", "spdifrx")
            if name.startswith("dac") and "_" not in name: name = "dac_{}".format(name);
            if any(name == n for n in ["sdio", "sdmmc2", "sdmmc1"]): continue
            if len(name.split("_")) < 2: name = "{}_default".format(name);
            driver, inst, name = split_af(name)

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

            if driver is None: # peripheral is not part of this device
                dma_dumped.append( (instance, stream, name) )
                continue
            mode = [v[4:].lower() for v in rv("Mode")]
            for sname in ([None] if name == "default" else name.split("/")):
                signal = {
                    "driver": driver,
                    "name": sname,
                    "direction": [v[4:].replace("PERIPH", "p").replace("MEMORY", "m").replace("_TO_", "2") for v in rv("Direction")],
                    "mode": mode,
                    "increase": "ENABLE" in rv("PeriphInc", ["DMA_PINC_ENABLE"])[0],
                }
                if inst: signal["instance"] = inst;
                remaps = stm32_data.getDmaRemap(did, instance, channel, driver, inst, sname)
                if remaps: signal["remap"] = remaps;
                dma_streams[instance][stream][channel].append(signal)
                # print(instance, stream, channel)
                # print(signal)

        # Manually handle condition expressions from XML for
        # (STM32F030CCTx|STM32F030RCTx) and (STM32F070CBTx|STM32F070RBTx)
        if did.family in ['f0']:
            if (did.name == '30' and did.size == 'c'):
                dma_streams['1'].pop('6')
                dma_streams['1'].pop('7')
                dma_streams.pop('2')
            if (did.name == '70' and did.size == 'b'):
                dma_streams['1'].pop('6')
                dma_streams['1'].pop('7')

        # De-duplicate DMA signal entries
        def deduplicate_list(l):
            return [i for n, i in enumerate(l) if i not in l[n + 1:]]
        for stream in dma_streams:
            for channel in dma_streams[stream]:
                for signal in dma_streams[stream][channel]:
                    dma_streams[stream][channel][signal] = deduplicate_list(
                        dma_streams[stream][channel][signal])

        # if p["dma_naming"][1] == "request":
        #     print(did, dmaFile.filename)
        p["dma"] = dma_streams
        if len(dma_dumped):
            for instance, stream, name in sorted(dma_dumped):
                LOGGER.debug("DMA{}#{}: dumping {}".format(instance, stream, name))

        # If DMAMUX is used, add DMAMUX to DMA peripheral channel mappings
        if p["dma_naming"] == (None, "request", "signal"):
            # There can be multiple "//RefParameter[@Name="Instance"]" nodes constrained by
            # a <Condition> child node filtering by the STM32 die id
            # Try to match a node with condition first, if nothing matches choose the default one
            die_id = device_file.query('//Die')[0].text
            q = '//RefParameter[@Name="Instance"]/Condition[@Expression="%s"]/../PossibleValue/@Value' % die_id
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
                mux_channels.append({'position'     : mux_ch_position,
                                     'dma-instance' : int(m.group("instance")),
                                     'dma-channel'  : int(m.group("channel"))})
            p["dma_mux_channels"] = mux_channels

    if did.family == "f1":
        grouped_f1_signals = gpioFile.compactQuery('//GPIO_Pin/PinSignal/@Name')

    _seen_gpio = set()
    for pin in pins:
        rname = pin.get("Name")
        name = pin_name(rname)

        # the analog channels are only available in the Mcu file, not the GPIO file
        localSignals = device_file.compactQuery('//Pin[@Name="{}"]/Signal[not(@Name="GPIO")]/@Name'.format(rname))
        # print(name, localSignals)
        altFunctions = []

        if did.family == "f1":
            altFunctions = [ (s.lower(), "-1") for s in localSignals if s not in grouped_f1_signals]
        else:
            allSignals = gpioFile.compactQuery('//GPIO_Pin[@Name="{}"]/PinSignal/SpecificParameter[@Name="GPIO_AF"]/..'.format(rname))
            signalMap = { a.get("Name"): a[0][0].text.lower().replace("gpio_af", "")[:2].replace("_", "") for a in allSignals }
            altFunctions = [ (s.lower(), (signalMap[s] if s in signalMap else "-1")) for s in localSignals ]

        afs = []
        for af in altFunctions:
            for raf in split_multi_af(af[0]):
                naf = {}
                naf["driver"], naf["instance"], naf["name"] = raf
                naf["af"] = af[1] if int(af[1]) >= 0 else None
                if "exti" in naf["name"]: continue;
                afs.append(naf)

        gpio = (name[0], name[1], afs)
        if name not in _seen_gpio:
            gpios.append(gpio)
            _seen_gpio.add(name)
        # print(gpio[0].upper(), gpio[1], afs)
        # LOGGER.debug("{}{}: {} ->".format(gpio[0].upper(), gpio[1]))

    remaps = {}
    if did.family == "f1":
        for remap in gpioFile.compactQuery('//GPIO_Pin/PinSignal/RemapBlock/@Name'):
            module = remap.split("_")[0].lower()
            config = remap.split("_")[1].replace("REMAP", "").replace("IREMAP", "")
            mapping = stm32_data.getGpioRemapForModuleConfig(module, config)

            mpins = []
            for pin in gpioFile.compactQuery('//GPIO_Pin/PinSignal/RemapBlock[@Name="{}"]/..'.format(remap)):
                name = pin.getparent().get("Name")[:4].split("-")[0].split("/")[0].strip().lower()
                pport, ppin = name[1:2], name[2:]
                if not any([pp[0] == pport and pp[1] == ppin for pp in gpios]):
                    continue
                mmm = {"port": pport, "pin": ppin}
                driver, _, name = split_af(pin.get("Name").lower())
                if driver is None: continue;
                mmm["name"] = name;
                mpins.append(mmm)

            if module not in remaps:
                driver, instance, _ = split_af(module + "_lol")
                if not driver: continue;
                remaps[module] = {
                    "mask": mapping["mask"],
                    "position": mapping["position"],
                    "groups": {},
                    "driver": driver,
                    "instance": instance,
                }
            if len(mpins) > 0:
                remaps[module]["groups"][mapping["mapping"]] = mpins
                LOGGER.debug("{:<20}{}".format(module + "_" + config, ["{}{}:{}".format(b["port"], b["pin"], b["name"]) for b in mpins]))

        # import json
        # print(json.dumps(remaps, indent=4))

    p["remaps"] = remaps
    p["gpios"] = gpios

    return p


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
