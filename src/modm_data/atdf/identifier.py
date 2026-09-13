# Copyright 2013, Kevin Läufer
# Copyright 2013, Niklas Hauser
# Copyright 2016, Fabian Greif
# SPDX-License-Identifier: MPL-2.0

import re

from ..kg import DeviceIdentifier


def avr_did_from_string(string: str) -> DeviceIdentifier | None:
    """
    Parses AVR device strings, for example, `atmega328p-au` or `at90can128-16au`.

    :return: The device identifier or None if the string cannot be parsed.
    """
    string = string.lower()
    if not string.startswith("at"):
        return None

    match_string = r"at(?P<family>tiny|mega|xmega)(?P<name>\d+)"
    if string.startswith("at90"):
        match_string = r"at(?P<family>90)(?P<type>can|pwm|usb)(?P<name>\d+)-(?P<speed>\d+)(?P<package>\w+)"

    if (match := re.search(match_string, string)) is None:
        return None

    i = DeviceIdentifier()
    i.set("platform", "avr")
    i.set("family", match.group("family").lower())
    i.set("name", match.group("name").lower())

    if i.family == "90":
        i.naming_schema = "at{family}{type}{name}-{speed}{package}"
        i.set("type", match.group("type").lower())
        i.set("speed", match.group("speed"))
        i.set("package", match.group("package"))
        return i

    if i.family in ["tiny", "mega"]:
        i.naming_schema = "at{family}{name}{type}-{speed}{package}"
        search = "at" + i.family + i.name + r"(?P<type>\w*)-(?P<speed>\d*)(?P<package>\w+)"
        if match := re.search(search, string):
            i.set("type", match.group("type").lower())
            i.set("speed", match.group("speed"))
            i.set("package", match.group("package"))
            return i
        return None

    # xmega
    i.naming_schema = "at{family}{name}{type}{pin}"
    search = "at" + i.family + i.name + r"(?P<type>[A-Ea-e]?[1-5]?)(?P<package>[Bb]?[Uu]?)"
    if match := re.search(search, string):
        if match.group("type") != "":
            i.set("type", match.group("type").lower())
        if match.group("package") != "":
            i.set("pin", match.group("package"))
    return i


def sam_family_from_series(series: str) -> str:
    """:return: The SAM family name for a series, for example, `D1x/D2x/DAx` for `d21`."""
    if series[0] == "c" and series[1] == "2":
        return "C2x"
    elif series[0] == "d":
        if series[1] == "5":
            return "D5x/E5x"
        elif series[1] in ("0", "1", "2", "a"):
            return "D1x/D2x/DAx"
    elif series[0] == "e" and series[1] == "5":
        return "D5x/E5x"
    elif series[0] == "g" and series[1] == "5":
        return "G5x"
    elif series[0] == "l":
        if series[1] == "1":
            return "L1x"
        elif series[1] == "2":
            return "L2x"
    elif series[0] in ("e", "s", "v") and series[1] == "7":
        return "E7x/S7x/V7x"
    elif series[0] == "4":
        return "4"
    raise ValueError(f"Unsupported SAM series '{series}'")


def sam_did_from_string(string: str) -> DeviceIdentifier:
    """
    Parses SAM device strings, for example, `ATSAMD21E15A-MUT`, organized as
    `{platform}{series}{pin}{flash}{variant}-{package}{grade}`.
    """
    string = string.lower()
    if string.startswith("sam") or string.startswith("atsam"):
        if string.startswith("atsam4"):
            match_string = (
                r"sam(?P<series>\d\w)(?P<flash>\d{1,2})(?P<pin>\w)(?P<variant>\w)?-(?P<package>\w)(?P<grade>\w)"
            )
        else:
            match_string = r"sam(?P<series>\w((\d{2})|(\w\d)))(?P<pin>\w)(?P<flash>\d{2})(?P<variant>\w)?-(?P<package>\w\w*)(?P<grade>\w)$"
        if match := re.search(match_string, string):
            i = DeviceIdentifier("{platform}{series}{pin}{flash}{variant}-{package}{grade}")
            i.set("platform", "sam")
            i.set("series", match.group("series"))
            i.set("pin", match.group("pin"))
            i.set("flash", match.group("flash"))
            i.set("variant", match.group("variant") or "")
            i.set("package", match.group("package"))
            i.set("grade", match.group("grade"))
            i.set("family", sam_family_from_series(match.group("series")).lower())
            return i

    raise ValueError(f"Unknown identifier '{string}'!")
