# Copyright 2022, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import logging

from collections import defaultdict

from ..header import Header as CmsisHeader
from ...utils import ext_path


LOGGER = logging.getLogger(__file__)


def getDefineForDevice(device_id, familyDefines):
    if len(familyDefines) == 1:
        return familyDefines[0]

    # get all defines for this device name
    devName = "STM32{}{}".format(device_id.family.upper(), device_id.name.upper())

    # Map STM32WL33 -> STM32WL3X
    if device_id.family == "wl" and devName[7:9] in ["30", "31", "33"]:
        devName = devName[:-1] + "X"

    deviceDefines = sorted([define for define in familyDefines if define.startswith(devName)])
    # if there is only one define thats the one
    if len(deviceDefines) == 1:
        return deviceDefines[0]

    # now we match for the size-id.
    devNameMatch = devName + "x{}".format(device_id.size.upper())
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    # now we match for the pin-id.
    devNameMatch = devName + "{}x".format(device_id.pin.upper())
    for define in deviceDefines:
        if devNameMatch <= define:
            return define

    return None


class Header(CmsisHeader):
    _HEADER_PATH = ext_path("stmicro/header")
    _CACHE_FAMILY = defaultdict(dict)

    def __init__(self, did, family_header_file, define):
        self.did = did
        self.family_folder = family_header_file
        if "xx" not in self.family_folder:
            self.family_folder += "x"
        self.cmsis_folder = Header._HEADER_PATH / self.family_folder / "Include"
        self.family_header_file = "{}.h".format(family_header_file)

        self.family_defines = self._get_family_defines()
        self.define = define[:9].upper() + define[9:]
        if self.define not in self.family_defines:
            self.define = getDefineForDevice(self.did, self.family_defines)
        self.is_valid = self.define is not None
        if not self.is_valid:
            return

        self.header_file = "{}.h".format(self.define.lower())
        substitutions = {
            # r"/\* +?(Legacy defines|Legacy aliases|Old .*? legacy purpose|Aliases for .*?) +?\*/.*?\n\n": "",
            r"/\* +?Legacy (aliases|defines|registers naming) +?\*/.*?\n\n": "",
            r"/\* +?Old .*? legacy purpose +?\*/.*?\n\n": "",
            r"/\* +?Aliases for .*? +?\*/.*?\n\n": "",
            # r"( 0x[0-9A-F]+)\)                 ": r"\1U",
            # r"#define.*?/\*!<.*? Legacy .*?\*/\n": "",
        }
        super().__init__(self.cmsis_folder / self.header_file, substitutions)

    @property
    def interrupt_table(self):
        if "vectors" not in self._cache:
            interrupt_enum = [i["values"] for i in self.header.enums if i["name"] == "IRQn_Type"][0]
            vectors = [
                {"position": int(str(i["value"]).replace(" ", "")), "name": i["name"][:-5]} for i in interrupt_enum
            ]
            self._cache["vectors"] = vectors
        return self._cache["vectors"]

    def _get_family_defines(self):
        if self.family_folder not in Header._CACHE_FAMILY:
            content = (self.cmsis_folder / self.family_header_file).read_text(encoding="utf-8", errors="replace")
            defines = []
            for include in re.findall(r'#include +"(stm32.*?(?<!_hal))\.h"', content):
                define = re.search(rf"defined *\( *({include}) *\)", content, flags=re.IGNORECASE)
                defines.append(define.group(1))
            Header._CACHE_FAMILY[self.family_folder]["family_defines"] = defines
        return Header._CACHE_FAMILY[self.family_folder]["family_defines"]
