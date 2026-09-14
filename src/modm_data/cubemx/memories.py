# Copyright 2017, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# Memories

The memories are primarily taken from the CMSIS-Pack device family pack (DFP),
which however does not list all memories and sometimes merges multiple SRAMs.
The missing information is derived from the CMSIS header, for example,
`SRAM2_BASE`, `CCMDATARAM_END`, `BKPSRAM_BASE`, and `SRAM1_SIZE_MAX`, and from
the CubeMX memory region files, which contain all RAM regions of the STM32H7
and STM32U5 devices.
"""

import re
from functools import cache
from ..utils import ext_path, XmlReader

_MEMORY_PATH = ext_path("stmicro/cubemx/mcu/memory")

_RENAMES = {
    "irom1": "flash",
    "main_flash": "flash",
    "flash-secure": "flash_s",
    "flash-non-secure": "flash_ns",
    "flash_bank1": "flash1",
    "flash_bank2": "flash2",
    "iram1": "sram1",
    "iram2": "sram2",
    "sram-secure": "sram_s",
    "sram-non-secure": "sram_ns",
    "sram1_2": "sram1",
    "ram_d1": "d1_sram",
    "ram_d2": "d2_sram1",
    "ram_d2s2": "d2_sram2",
    "ram_d2s3": "d2_sram3",
    "ram_d3": "d3_sram",
    "axi_sram": "d1_sram",
    "ahb_sram": "d2_sram1",
    "ccm_ram": "ccm",
    "dtcmram": "dtcm",
    "dtcm_ram": "dtcm",
    "itcm_ram": "itcm",
    "bkp_sram": "backup",
}

# Corrections of the CMSIS header defines, which were verified against the reference manuals.
_HEADER_FIXES = [
    # RM0090: The STM32F427 header does not define the SRAM3 like the STM32F437 header
    ({"family": ["f4"], "name": ["27"]}, {"SRAM3_BASE": 0x20020000}),
]

# RM0033, RM0090, RM0390, RM0433: The size of the backup SRAM if not defined in the header
_BACKUP_SRAM_SIZE = 4 * 1024

# RM0316: The CCM SRAM sizes of the STM32F3x8 devices are neither defined in the DFP nor
# in the headers and the DFP SRAM size of some devices includes the CCM SRAM size.
_F3_CCM_SRAM = [
    ({"name": ["28"]}, 4, 12),
    ({"name": ["58"]}, 8, 40),
    ({"name": ["98"]}, 16, 64),
]


def _rename(name: str) -> str:
    # EEPROM is not explicitly listed in STM32 memory map, but we can deduce it
    if "_eeprom.flm" in name:
        return "eeprom"
    return _RENAMES.get(name, name)


@cache
def _cubemx_ram_regions(die: str, core: str | None) -> dict[str, tuple[int, int]]:
    """:return: The RAM regions of the CubeMX memory file of the die as name to (start, size)."""
    files = sorted(_MEMORY_PATH.glob(f"STM32_{die}_*.xml"))
    dual = [f for f in files if f.stem.endswith("_DUAL")]
    files = dual if core and dual else [f for f in files if f not in dual]
    if not files:
        return {}
    regions = {}
    for memory in XmlReader(files[-1]).query('//memory[@type="RAM"]'):
        if core and memory.get("Pname", f"C{core.upper()}") != f"C{core.upper()}":
            continue
        regions[memory.get("name")] = (int(memory.get("start"), 0), int(memory.get("size"), 0))
    return regions


def _split(mems: dict, name: str, parts: list[tuple[str, int, int]]):
    """Splits a memory into the parts of (name, start, size) if they cover exactly the memory."""
    if (memory := mems.get(name)) is None or len(parts) < 2:
        return
    parts = sorted(parts, key=lambda p: p[1])
    end = memory["start"]
    for _, start, size in parts:
        if start != end:
            return
        end += size
    if end != memory["start"] + memory["size"]:
        return
    del mems[name]
    for part, start, size in parts:
        mems[part] = {"access": memory["access"], "start": start, "size": size}


def _matches(did, selector: dict) -> bool:
    return all(did.get(key) in values for key, values in selector.items())


def _split_sram(mems: dict, macro):
    """Splits the SRAM at the SRAMx_BASE addresses and limits the sizes to the SRAMx_SIZE_MAX of the header."""
    name = "sram" if "sram" in mems else "sram1"
    if (sram := mems.get(name)) is None or macro("SRAM1_BASE") != sram["start"]:
        return
    end = sram["start"] + sram["size"]
    bases = [(1, sram["start"])]
    for index in range(2, 4):
        if (base := macro(f"SRAM{index}_BASE")) is not None and bases[-1][1] < base < end:
            bases.append((index, base))
    if len(bases) > 1:
        parts = [(f"sram{i}", base, limit - base) for (i, base), (_, limit) in zip(bases, bases[1:] + [(0, end)])]
        _split(mems, name, parts)
        name = "sram1"
    for index in range(1, 4):
        sram = mems.get(name if index == 1 else f"sram{index}")
        limit = macro(f"SRAM{index}_SIZE_MAX") or macro(f"SRAM{index}_SIZE")
        if sram is not None and limit is not None:
            sram["size"] = min(sram["size"], limit)


def _add_memory(mems: dict, name: str, start: int, size: int, access: str = "rwx"):
    if name not in mems and start is not None and size:
        mems[name] = {"access": access, "start": start, "size": size}


def memories(did, dfp_memories: dict[str, dict], header, die: str) -> list[dict]:
    """
    Computes the memories of a device.

    :param did: The device identifier.
    :param dfp_memories: Memories and flash algorithms from the DFP as name to access, start, size, and alias.
    :param header: The CMSIS header of the device.
    :param die: The CubeMX die name, for example, `DIE450`.
    :return: A list of memories with name, access, start, size, and optional alias.
    """
    mems = {}
    for name, data in dfp_memories.items():
        name = _rename(name)
        if ".flm" in name:
            continue
        data = dict(data)
        if alias := data.pop("alias", ""):
            data["alias"] = _RENAMES.get(alias, alias)

        # Fix access for FLASH and EEPROM
        if "flash" in name:
            data["access"] = "rx"
        if "eeprom" in name:
            data["access"] = "r"
        # On STM32F4 and STM32L4 the CCM memory is rw only
        if did.family in ["f4", "l4"] and data["start"] == 0x10000000:
            name = "ccm"
            data["access"] = "rw"
        mems[name] = data

    regions = _cubemx_ram_regions(die, did.get("core"))
    fixes = {}
    for selector, defines in _HEADER_FIXES:
        if _matches(did, selector):
            fixes.update(defines)

    def macro(name: str) -> int | None:
        return fixes[name] if name in fixes else header.define_value(name)

    # Split merged SRAMs and limit their sizes
    _split_sram(mems, macro)
    ahb = [(f"d2_sram{m.group(1)}", *r) for n, r in regions.items() if (m := re.fullmatch(r"RAM(\d)_AHB", n))]
    _split(mems, "d2_sram1", ahb)

    # Add missing CCM SRAM
    if (end := macro("CCMDATARAM_END")) is not None:
        _add_memory(mems, "ccm", macro("CCMDATARAM_BASE"), end + 1 - macro("CCMDATARAM_BASE"), access="rw")
    if (size := macro("CCMSRAM_SIZE")) is not None:
        _add_memory(mems, "ccm", macro("CCMSRAM_BASE"), size)
    if did.family == "f3":
        for selector, ccm, sram in _F3_CCM_SRAM:
            if _matches(did, selector):
                _add_memory(mems, "ccm", macro("CCMDATARAM_BASE"), ccm * 1024)
                mems["sram"]["size"] = sram * 1024

    # Add missing backup SRAM
    if not any(name in mems for name in ("backup", "bkpsram_ns")):
        for name in ("BKPSRAM", "BKPSRAM_NS"):
            if name in regions:
                _add_memory(mems, "backup", *regions[name])
        if (start := macro("BKPSRAM_BASE_NS") or macro("BKPSRAM_BASE") or macro("D3_BKPSRAM_BASE")) is not None:
            _add_memory(mems, "backup", start, macro("BKPSRAM_SIZE") or _BACKUP_SRAM_SIZE)

    # Add missing ITCM and D3 SRAM of STM32H7
    if "RAM_ITCM" in regions:
        _add_memory(mems, "itcm", *regions["RAM_ITCM"])
    if did.family == "h7" and (d3 := regions.get("RAM_AHB_D4", regions.get("RAM_AHB_D3"))):
        _add_memory(mems, "d3_sram", *d3)

    # Correct memories for specific devices
    if did.family == "f7":
        # Fix missing alias, ITCM_FLASH is faster
        mems["flash"]["alias"] = "itcm_flash"
        if did.name in ["30", "50"]:
            # The DFP lists 1MB of Flash for the value line devices with 64kB
            mems["flash"]["size"] = mems["itcm_flash"]["size"] = 64 * 1024

    elif did.family == "l4":
        # The DFP lists the wrong Flash size for some packages
        if did.string.startswith(("stm32l471zgj", "stm32l476zgt")):
            mems["flash"]["size"] = 1024 * 1024
        elif did.string.startswith("stm32l496aei"):
            mems["flash"]["size"] = 512 * 1024

    elif did.family == "h7":
        for name, data in mems.items():
            if "flash" not in name:
                data["access"] = "rwx"

    return [{"name": name} | data for name, data in mems.items()]
