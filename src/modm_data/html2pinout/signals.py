# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
The alternate and additional functions of the GPIOs of the STM32 device
mounted on a board and what the board itself already uses them for, taken from
the CubeMX database.
"""

import re
from functools import cache
from pathlib import Path
from xml.etree import ElementTree
from ..utils import ext_path

_NS = "{http://mcd.rou.st.com/modules.php?name=mcu}"


# What the GPIO labels of the board description say about their use on the board
_USES = {
    "debug": r"SW(DIO|CLK|O|0)|JT|^T(MS|CK|DI|DO)$|TRACE|DEBUG",
    "vcp": r"VCP|STLK|STLINK|^(LP)?US?ART\d*_[TR]X$",
    "ethernet": r"RMII?_|^ETH",
    "usb": r"USB|UCPD|VBUS",
    "clock": r"OSC|MCO",
    "led": r"^LD\d|LED",
    "button": r"BUTTON|BTN|^B\d|^USER",
}


def _cubemx_path() -> Path:
    return ext_path("stmicro/cubemx")


def _mcu_path() -> Path:
    return _cubemx_path() / "mcu"


@cache
def _families() -> list[tuple[str, str]]:
    root = ElementTree.parse(_mcu_path() / "families.xml").getroot()
    return sorted((mcu.get("RefName"), mcu.get("Name")) for mcu in root.iter("Mcu"))


def device_for_board(board: str) -> tuple[str, str] | None:
    """
    :param board: A board name like `NUCLEO-L452RE-P`.
    :return: The name of the mounted device and of its CubeMX description, or `None` if unknown.
    """
    if not (match := re.fullmatch(r"NUCLEO-(\w+?)(?:-([PQ]))?", board)):
        return None
    smps = match.group(2) or ""
    # Some board names end in a revision digit, which is not part of the device name
    for part in ("STM32" + match.group(1), "STM32" + match.group(1)[:-1]):
        devices = [d for d in _families() if re.fullmatch(rf"{part}[A-Z]x{smps}?", d[0])]
        # The boards prefer the LQFP package and the SMPS variant may have a suffix
        order = [lambda d: d[0] == f"{part}Tx{smps}", lambda d: d[0] == f"{part}Tx", lambda d: True]
        if device := next((d for check in order for d in devices if check(d)), None):
            return device
    return None


@cache
def device_signals(name: str) -> dict[str, dict[str, int | None]]:
    """
    :param name: The name of a CubeMX device description like `STM32F401R(D-E)Tx`.
    :return: `gpio -> signal -> alternate function number` or `None` for additional functions.
    """
    mcu = ElementTree.parse(_mcu_path() / f"{name}.xml").getroot()
    version = mcu.find(f"{_NS}IP[@Name='GPIO']").get("Version")
    gpio = ElementTree.parse(_mcu_path() / f"IP/GPIO-{version}_Modes.xml").getroot()
    ns = gpio.tag[: gpio.tag.index("}") + 1]
    afs = {}
    for pin in gpio.iter(f"{ns}GPIO_Pin"):
        for signal in pin.iter(f"{ns}PinSignal"):
            value = signal.findtext(f"{ns}SpecificParameter[@Name='GPIO_AF']/{ns}PossibleValue") or ""
            if af := re.match(r"GPIO_AF(\d+)", value):
                afs[(pin.get("Name"), signal.get("Name"))] = int(af.group(1))

    signals = {}
    for pin in mcu.iter(f"{_NS}Pin"):
        # Pins have names like PA0-WKUP, PC14-OSC32_IN or PA9 [PA11]
        if not (port := re.match(r"P[A-Z]\d+", pin.get("Name"))):
            continue
        for signal in pin.iter(f"{_NS}Signal"):
            if (signal := signal.get("Name")) != "GPIO":
                signals.setdefault(port.group(0), {})[signal] = afs.get((pin.get("Name"), signal))
    return {gpio: dict(sorted(names.items())) for gpio, names in signals.items()}


def _board_files(board: str) -> list[Path]:
    return sorted((_cubemx_path() / "plugins/boardmanager/boards").glob(f"*_Nucleo_{board}_*_Board_AllConfig.ioc"))


def has_board_file(board: str) -> bool:
    """:return: whether CubeMX describes a board with exactly this name."""
    return bool(_board_files(board))


def board_uses(board: str) -> dict[str, dict[str, str]]:
    """
    :param board: A board name like `NUCLEO-F429ZI`.
    :return: `gpio -> {use, label, part, signal}` for all GPIOs that are connected to something on the board
             itself, like the ST-LINK, an Ethernet PHY, a crystal, LEDs or buttons.
    """
    if not (paths := _board_files(board)):
        return {}
    pins = {}
    # The shortest name is the default configuration without TrustZone or multi-core variants
    for line in min(paths, key=lambda p: len(p.name)).read_text(errors="replace").splitlines():
        if match := re.match(r"(P[A-Z]\d+)[^.=]*\.(GPIO_Label|Signal)=(.*)", line):
            pins.setdefault(match.group(1), {})[match.group(2)] = match.group(3).strip()

    uses = {}
    for gpio, pin in pins.items():
        label, _, part = pin.get("GPIO_Label", "").partition("[")
        label, part, signal = label.strip(), part.strip(" ]"), pin.get("Signal", "")
        name = (label or signal).upper()
        use = next((use for use, pattern in _USES.items() if re.search(pattern, name)), "other")
        # Neither the labels of the ARDUINO connector pins nor a default peripheral configuration occupy the pin
        if re.match(r"ARD", label) or (use == "other" and label in {"", signal}):
            continue
        uses[gpio] = {k: v for k, v in {"use": use, "label": label, "part": part, "signal": signal}.items() if v}
    return dict(sorted(uses.items()))
