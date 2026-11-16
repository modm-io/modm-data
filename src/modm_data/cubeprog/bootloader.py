# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import xml.etree.ElementTree as ET
from functools import cache

from ..utils import ext_path

_DB_PATH = ext_path("stmicro/cubeprog/Data_Base")

# Vector table of the system memory bootloader by device ID (DEV_ID, the CubeMX die and the
# PID column of AN2606 Table 226). The CubeProgrammer database has no system memory region,
# so these are the start of the system memory from AN2606 v71 Table 226 except where noted.
# Dies without an entry cannot jump into their bootloader from the application.
_ENTRY = {
    # STM32C0
    0x443: 0x1FFF0000, 0x453: 0x1FFF0000, 0x44C: 0x1FFF0000, 0x493: 0x1FFF0000, 0x44D: 0x1FFF0000,
    # STM32C5: AN2606 lists the system memory from 0x0BF80080, which is not aligned for the
    # vector table of a Cortex-M33 with more than 32 vectors. Not verified on hardware.
    0x44E: 0x0BF80000, 0x44F: 0x0BF80000, 0x45A: 0x0BF80000,
    # STM32F0: the F04x and F070x6 (0x445) check for an empty flash and jump back into the
    # application (AN2606 Section 16.1 and 17.1)
    0x440: 0x1FFFEC00, 0x444: 0x1FFFEC00, 0x442: 0x1FFFD800, 0x448: 0x1FFFC800,
    # STM32F1
    0x412: 0x1FFFF000, 0x410: 0x1FFFF000, 0x414: 0x1FFFF000, 0x420: 0x1FFFF000, 0x428: 0x1FFFF000,
    0x418: 0x1FFFB000, 0x430: 0x1FFFE000,
    # STM32F2, F3, F4, F7
    0x411: 0x1FFF0000,
    0x432: 0x1FFFD800, 0x422: 0x1FFFD800, 0x439: 0x1FFFD800, 0x438: 0x1FFFD800, 0x446: 0x1FFFD800,
    0x413: 0x1FFF0000, 0x419: 0x1FFF0000, 0x423: 0x1FFF0000, 0x433: 0x1FFF0000, 0x458: 0x1FFF0000,
    0x431: 0x1FFF0000, 0x441: 0x1FFF0000, 0x421: 0x1FFF0000, 0x434: 0x1FFF0000, 0x463: 0x1FFF0000,
    0x452: 0x1FF00000, 0x449: 0x1FF00000, 0x451: 0x1FF00000,
    # STM32G0, G4
    0x466: 0x1FFF0000, 0x456: 0x1FFF0000, 0x460: 0x1FFF0000, 0x467: 0x1FFF0000,
    0x468: 0x1FFF0000, 0x469: 0x1FFF0000, 0x479: 0x1FFF0000,
    # STM32H5: the H543/553 (0x47C) are not in AN2606, their bootloader ID is at 0x0BFADFFE
    # like on the STM32G5, so their bootloader is elsewhere than on the H523/533 (0x478).
    0x474: 0x0BF87000, 0x478: 0x0BF97000, 0x484: 0x0BF97000, 0x47A: 0x0BFAC000,
    # STM32H7: the "bootloader start address" of AN2606 Section 59.1 to 61.1, not the start of
    # the system memory. The H7R/S starts at its system memory.
    0x483: 0x1FF09800, 0x450: 0x1FF09800, 0x480: 0x1FF0A000, 0x485: 0x1FF18000,
    # STM32L0: AN2606 Section 4.1 claims that no STM32L0 can jump from the application, but only
    # the L01x/02x (0x457) check for an empty flash (AN2606 Section 63.1), the L05x jumps fine.
    0x425: 0x1FF00000, 0x417: 0x1FF00000, 0x447: 0x1FF00000,
    # STM32L1, L4, L5
    0x416: 0x1FF00000, 0x429: 0x1FF00000, 0x427: 0x1FF00000, 0x436: 0x1FF00000, 0x437: 0x1FF00000,
    0x464: 0x1FFF0000, 0x435: 0x1FFF0000, 0x462: 0x1FFF0000, 0x415: 0x1FFF0000, 0x461: 0x1FFF0000,
    0x470: 0x1FFF0000, 0x471: 0x1FFF0000,
    0x472: 0x0BF90000,
    # STM32U0, U3, U5: AN2606 lists only the U375/385 and U3B5/3C5, RM0487 FLASH_BOOT0R has the
    # same bootloader address for all U3
    0x459: 0x1FFF0000, 0x489: 0x1FFF0000,
    0x454: 0x0BF8F000, 0x42A: 0x0BF8F000, 0x42B: 0x0BF8F000, 0x42C: 0x0BF8F000,
    0x455: 0x0BF90000, 0x482: 0x0BF90000, 0x481: 0x0BF90000, 0x476: 0x0BF90000,
    # STM32WB, WBA, WL
    0x494: 0x1FFF0000, 0x495: 0x1FFF0000,
    0x4B2: 0x0BF85000, 0x492: 0x0BF88000, 0x4B0: 0x0BF90000,
    0x497: 0x1FFF0000,
}  # fmt: skip

# The bootloader ID addresses are from the CubeProgrammer database, which matches AN2606
# Table 3 except for the H523/533, where AN2606 has the H503 address 0x0BF8FFFE instead of the
# end of their bootloader at 0x0BF9FAFE.


@cache
def _bootloader_ids() -> dict[int, int]:
    """:return: the address of the bootloader ID of every device ID in the database."""
    ids = {}
    for file in sorted(_DB_PATH.glob("STM32_Prog_DB_0x*.xml")):
        device = ET.parse(file).getroot().find("Device")
        if (node := next(device.iter("BootloaderVersion"), None)) is not None:
            ids[int(device.findtext("DeviceID"), 16)] = int(node.get("address"), 16)
    return ids


def system_bootloader(die: str) -> dict[str, int]:
    """
    The system memory bootloader of a die.

    :param die: the CubeMX die of the device, for example 'DIE413'.
    :return: the vector table address `entry`, if the application can jump into the
             bootloader, and the address of the bootloader ID `id-address`, if the device has
             one. Empty if neither is known.
    """
    did = int(die.upper().removeprefix("DIE"), 16)
    result = {}
    if (entry := _ENTRY.get(did)) is not None:
        result["entry"] = entry
    if (id_address := _bootloader_ids().get(did)) is not None:
        result["id-address"] = id_address
    return result
