# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

from ..identifier import DeviceIdentifier


def did_from_string(string) -> DeviceIdentifier:
    string = string.lower()

    if string.startswith("stm32"):
        schema = "{platform}{family}{name}{pin}{size}{package}{temperature}{variant}"
        if "@" in string:
            schema += "@{core}"
        i = DeviceIdentifier(schema)
        i.set("platform", "stm32")
        i.set("family", string[5:7])
        i.set("name", string[7:9])
        i.set("pin", string[9])
        i.set("size", string[10])
        i.set("package", string[11])
        i.set("temperature", string[12])
        if "@" in string:
            string, core = string.split("@")
            i.set("core", core)
        if len(string) >= 14:
            i.set("variant", string[13])
        else:
            i.set("variant", "")
        return i

    raise ValueError(f"Unknown identifier '{string}'!")
