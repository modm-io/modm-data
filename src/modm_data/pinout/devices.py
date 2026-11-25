# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import logging
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

LOGGER = logging.getLogger(__name__)

STM32_FAMILIES = [
    "c0",
    "c5",
    "f0",
    "f1",
    "f2",
    "f3",
    "f4",
    "f7",
    "g0",
    "g4",
    "g5",
    "h5",
    "h7",
    "l0",
    "l1",
    "l4",
    "l5",
    "u0",
    "u3",
    "u5",
    "wb",
    "wl",
]
AVR_PREFIXES = ["at90", "attiny", "atmega"]
SAM_PREFIXES = ["samda", "samd1", "samd2", "samd09", "saml2", "samg5", "samd5", "same5", "same7", "sams7", "samv7"]
NRF_PREFIXES = ["nrf51", "nrf52", "nrf53"]
RP_PREFIXES = ["rp2040", "rp2350"]

_GPIO_NAME = re.compile(r"\bP(?P<port>[A-Z])(?P<pin>\d+)\b|\bP(?P<nport>\d+)[._](?P<npin>\d+)\b")


# ================================== VENDORS ==================================
# Every vendor reader returns the pinout of a device as a dictionary with the package
# `{name, pins: [{position, name, type}]}` or `None`, the GPIOs `[{port, pin, name, signals}]`
# with the signals `{driver, instance, name, af}`, and the peripheral-wide `shared` functions.
def stm32_partnames() -> list[str]:
    from .. import cubemx

    return sorted({p for family in STM32_FAMILIES for p in cubemx.devices_from_prefix(f"STM32{family.upper()}")})


def stm32_devices(partname: str) -> list[tuple[str, str, dict]]:
    """:return: (chip id, source, pinout) of every temperature range and core of the device."""
    from .. import cubemx

    devices = []
    for p in cubemx.devices_from_partname(partname):
        package = {
            "name": p["package"],
            "pins": [
                {
                    "position": pin["position"],
                    "name": pin["name"],
                    "type": None if "I/O" in pin["type"] else pin["type"],
                }
                # The pins of the same position are ordered by their remap variant
                for pin in sorted(p["pinout"], key=lambda pin: pin.get("variant", ""))
            ],
        }
        gpios = []
        for port, pin, signals in p["gpios"]:
            gpio_signals = []
            for s in signals:
                # EXTI lines are available on all pins and not listed as signals
                if "exti" in s["name"]:
                    continue
                instance = s["instance"]
                # Only one SPDIFRX exists, but some STM32H7 name it with instance 1
                if s["driver"] == "spdifrx" and instance == "1":
                    instance = None
                gpio_signals.append({"driver": s["driver"], "instance": instance, "name": s["name"], "af": s["af"]})
            gpio_signals.sort(key=lambda s: (int(s["af"] or -1), s["driver"] or "", s["instance"] or "", s["name"]))
            gpios.append({"port": port, "pin": pin, "signals": gpio_signals})
        pinout = {"package": package, "gpios": gpios, "shared": []}
        for temperature in cubemx.temperature_codes(partname):
            did = p["id"].copy()
            did.set("temperature", temperature)
            devices.append((did.string, f"stm32/stm32{did.family}{did.name}", pinout))
    return devices


def _atdf_gpios(p, platform: str) -> list[dict]:
    gpios = []
    for port, pin in p["gpios"]:
        signals = []
        for s in (s for s in p["signals"] if s["pad"] == f"p{port}{pin}"):
            driver, instance, name = s["module"], s["instance"], s["group"]
            if platform == "avr":
                if driver.startswith("tc"):
                    driver = "tc"
                if driver == "cpu":
                    driver = instance = "core"
                if name not in (driver, "int") and "index" in s:
                    name += s["index"]
            signal = {"driver": driver, "instance": None if instance == driver else instance.replace(driver, "")}
            if name not in (driver, "int"):
                signal["name"] = name
            elif "index" in s:
                signal["name"] = s["index"]
            else:
                LOGGER.error("%s has no name!", s)
                continue
            signals.append(signal)
        signals.sort(key=lambda s: (s["driver"], s["instance"] or "", s["name"]))
        gpios.append({"port": port.upper(), "pin": pin, "signals": signals})
    return gpios


def atdf_devices(platform: str, prefixes: list[str]) -> list[tuple[str, str, dict]]:
    from ..atdf import avr, sam

    module = avr if platform == "avr" else sam
    devices = []
    for prefix in prefixes:
        for path in module.device_files(prefix):
            for ordercode in module.devices_from_file(path):
                if (p := module.device_from_ordercode(path, ordercode)) is not None:
                    pinout = {"package": None, "gpios": _atdf_gpios(p, platform), "shared": []}
                    devices.append((p["id"].string, f"{platform}/{path.stem.lower()}", pinout))
    return devices


def _nrf_shared(p) -> tuple[list[str], dict]:
    signals = defaultdict(set)
    for signal in p["signals"]:
        signals[signal["driver"]].add(signal["name"] + signal.get("index", ""))
    for driver, names in p.get("fixed_signals", {}).items():
        signals[driver].update(names)

    pin_signals = defaultdict(list)
    for key, tags in p.get("pin_specials", {}).items():
        for tag in tags:
            if tag in ("spim4", "qspi", "trace"):
                continue
            if tag.startswith("spim4_"):
                entries = [("spim", "4", tag.split("_", 1)[1])]
            elif tag.startswith("qspi_"):
                entries = [("qspi", None, tag.split("_", 1)[1])]
            elif tag == "traceclk":
                entries = [("trace", None, "clk")]
            elif tag.startswith("tracedata"):
                entries = [("trace", None, tag.replace("tracedata", "data", 1))]
            elif tag == "swo":
                entries = [("trace", None, "swo")]
            elif tag in ("twi", "twim", "twis"):
                entries = [
                    (d, None, n) for d in ("twim", "twis", "twi") for n in ("scl", "sda") if n in signals.get(d, ())
                ]
            elif re.fullmatch(r"ain\d+", tag):
                # The analog inputs are fixed to their pin, not available on every pin
                entries = [(d, None, tag) for d, names in signals.items() if tag in names]
                for driver, _, name in entries:
                    signals[driver].discard(name)
            else:
                entries = [("special", None, tag)]
            for driver, instance, name in entries:
                signal = {"driver": driver, "instance": instance, "name": name, "af": None}
                if signal not in pin_signals[key]:
                    pin_signals[key].append(signal)

    instances = defaultdict(list)
    for module, instance in p["modules"]:
        if module not in ("fuses", "mtb", "systemcontrol", "systick", "hmatrixb", "hmatrix", "approtect"):
            instances[module].append(instance[len(module) :])
    # The other peripherals are connected to any pin
    shared = []
    for driver in sorted(instances):
        for instance in sorted(set(instances[driver])) if any(instances[driver]) else [None]:
            shared += [format_signal(driver, instance, name) for name in sorted(signals.get(driver, ()))]
    return list(dict.fromkeys(shared)), pin_signals


def nrf_devices(prefixes: list[str]) -> list[tuple[str, str, dict]]:
    from .. import nrfx
    from ..nrfx.pinout import package_code_map

    devices = []
    for prefix in prefixes:
        for path in nrfx.device_files(prefix):
            p = nrfx.device_from_file(path)
            did = p["id"]
            shared, pin_signals = _nrf_shared(p)
            gpios = [
                {"port": port, "pin": pin, "signals": pin_signals.get(f"{port}.{pin}", [])} for port, pin in p["gpios"]
            ]
            packages = p.get("pin_packages", [])
            codes = list(dict.fromkeys(c for package in packages for c in package.get("codes", [])))
            codes = codes or list(
                dict.fromkeys(c for cs in package_code_map.get(f"nrf{did.family}{did.series}", {}).values() for c in cs)
            )
            for code in codes or [None]:
                pdid = did.copy()
                if code is not None:
                    pdid.set("package", code)
                candidates = [pk for pk in packages if not pk.get("codes") or code in pk["codes"]]
                package = max(candidates, key=lambda pk: len(pk["pins"]), default=None)
                if package is not None:
                    package = {"name": package["name"], "pins": [{"type": None, **pin} for pin in package["pins"]]}
                pinout = {"package": package, "gpios": gpios, "shared": shared}
                devices.append((pdid.string, f"nrf/nrf{did.family}{did.series}", pinout))
    return devices


def rp_devices(prefixes: list[str]) -> list[tuple[str, str, dict]]:
    from .. import picosdk

    devices = []
    for prefix in prefixes:
        for p in (p for path in picosdk.device_files(prefix) for p in picosdk.device_from_file(path)):
            gpios = [
                {
                    "port": pin["bank"],
                    "pin": str(pin["idx"]),
                    "name": pin["name"],
                    "signals": sorted(
                        ({"instance": None, **s} for s in pin["signals"]), key=lambda s: (s["af"], s["name"])
                    ),
                }
                for pin in p["gpios"]
            ]
            devices.append((p["id"].string, f"rp/{p['id'].string}", {"package": None, "gpios": gpios, "shared": []}))
    return devices


# ================================== PAGES ===================================
def _payload(chip_id: str, source: str, device: dict) -> dict:
    package, rows = pin_rows(device)
    return {"chip_id": chip_id, "partname": chip_id, "source": source, "package": package, "rows": rows}


def _stm32_payloads(partname: str) -> list[dict]:
    try:
        return [_payload(*device) for device in stm32_devices(partname)]
    except Exception:
        LOGGER.exception(f"Skipping {partname}")
        return []


def payloads(jobs: int | None = None) -> list[dict]:
    """:return: the pinouts of all devices for the pages."""
    with ProcessPoolExecutor(jobs) as executor:
        result = [p for ps in executor.map(_stm32_payloads, stm32_partnames(), chunksize=8) for p in ps]
    devices = atdf_devices("avr", AVR_PREFIXES)
    devices += atdf_devices("sam", SAM_PREFIXES)
    devices += nrf_devices(NRF_PREFIXES)
    devices += rp_devices(RP_PREFIXES)
    result += [_payload(*device) for device in devices]
    return sorted(result, key=lambda p: p["chip_id"])


# =================================== ROWS ====================================
def format_signal(driver: str, instance: str | None, name: str | None) -> str:
    prefix = f"{driver.upper()}{instance or ''}"
    return f"{prefix}_{name.upper()}" if name else prefix


def _pin_sort_key(position: str) -> tuple[int, int | str]:
    return (0, int(position)) if position.isdigit() else (1, position)


def _functions(gpio: dict, shared: list[str]) -> list[str]:
    """The functions of a pin: the signals grouped by alternate function, then the additional ones."""
    alternate, additional = defaultdict(list), []
    for s in gpio["signals"]:
        label = format_signal(s["driver"], s["instance"], s["name"])
        if s.get("af") is None:
            additional.append(label)
        else:
            alternate[int(s["af"]) if str(s["af"]).isdigit() else 1_000_000].append(label)
    groups = ["/".join(dict.fromkeys(alternate[af])) for af in sorted(alternate)]
    return list(dict.fromkeys(groups + additional + shared))


def _gpio_key(name: str) -> tuple[str, str] | None:
    if (match := _GPIO_NAME.search(name.upper())) is None:
        return None
    return ((match["port"] or match["nport"]).lower(), str(int(match["pin"] or match["npin"])))


def pin_rows(pinout: dict) -> tuple[dict | None, list[dict]]:
    """
    Converts the pinout of a device into the rows of the pin matrix and the pins of the
    package diagram, which group the rows of the same position.

    :return: (package, rows) with the package `None` for devices without package data.
    """
    gpios = {(g["port"].lower(), str(int(g["pin"]))): g for g in pinout["gpios"]}
    rows = []
    if package := pinout["package"]:
        pins = sorted((p for p in package["pins"] if p["position"]), key=lambda p: _pin_sort_key(p["position"]))
        package_pins = {}
        for row_id, pin in enumerate(pins):
            gpio = gpios.get(_gpio_key(pin["name"]))
            functions = _functions(gpio, pinout["shared"]) if gpio else []
            rows.append(_row(row_id, pin["position"], pin["name"], functions))
            ptype = (pin["type"] or "io").lower()
            if (ppin := package_pins.get(pin["position"])) is None:
                package_pins[pin["position"]] = {
                    "row_id": row_id,
                    "row_ids": [row_id],
                    "position": pin["position"],
                    "short_name": pin["name"],
                    "type": ptype,
                }
            else:
                ppin["row_ids"].append(row_id)
                ppin["short_name"] = ppin["short_name"] or pin["name"]
                if ppin["type"] == "io":
                    ppin["type"] = ptype
        package = {"name": package["name"], "pins": list(package_pins.values())}
    else:
        pins = []
        for g in pinout["gpios"]:
            port = g["port"].upper()
            pins.append((f"{port}{g['pin']}", g.get("name") or f"P{port}{g['pin']}", _functions(g, pinout["shared"])))
        for row_id, (position, name, functions) in enumerate(sorted(pins, key=lambda p: _pin_sort_key(p[0]))):
            rows.append(_row(row_id, position, name, functions))
    return package, rows


def _row(row_id: int, position: str, name: str, functions: list[str]) -> dict:
    return {
        "row_id": row_id,
        "position": position,
        "short_name": name,
        "pin_label": f"{position} {name}".strip(),
        "functions": functions,
    }
