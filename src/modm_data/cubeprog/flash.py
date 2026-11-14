# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import re
import struct
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from ..utils import ext_path

LOGGER = logging.getLogger(__name__)
_DB_PATH = ext_path("stmicro/cubeprog/Data_Base")

# Conditions of the debug interface configurations that select the flash organization.
# The other conditions (readout protection, TrustZone, value line, ...) do not change it.
_SIZE_CONDITIONS = {"flashsize"}
_BANK_CONDITIONS = {"dbank", "dualbank", "db1m"}
# Dies whose largest flash always runs in dual-bank mode, although the database also lists a
# single-bank organization: RM0444 Table 12, the STM32G0B1/C1 with 512 KB ignores DUAL_BANK.
_ALWAYS_DUAL = {("DIE467", 512 * 1024)}


@dataclass(frozen=True)
class FlashLayout:
    banks: tuple[tuple[int, tuple[tuple[int, int], ...]], ...]
    """Start address of every bank and its groups of (size, count) pages or sectors."""
    options: tuple[tuple[str, int, int, int], ...] = ()
    """The option bit conditions of this layout as (name, register address, mask, value),
    empty if the layout does not depend on the option bits."""
    alignment: int = 0
    """Program unit in bytes, zero if unknown."""
    erased: int = 0xFF
    """Value of an erased byte."""

    @property
    def size(self) -> int:
        return sum(size * count for _, groups in self.banks for size, count in groups)

    def sectors(self) -> list[tuple[int, int]]:
        """:return: (address, size) of every page or sector."""
        sectors = []
        for start, groups in self.banks:
            for size, count in groups:
                for _ in range(count):
                    sectors.append((start, size))
                    start += size
        return sectors


def _groups(sizes: list[int]) -> tuple[tuple[int, int], ...]:
    groups = []
    for size in sizes:
        if groups and groups[-1][0] == size:
            groups[-1][1] += 1
        else:
            groups.append([size, 1])
    return tuple(tuple(g) for g in groups)


def _with_sectors(layout: FlashLayout, sectors: list[tuple[int, int]]) -> FlashLayout:
    """:return: the layout with its banks split from the sectors at the same bank starts."""
    starts = [start for start, _ in layout.banks] + [1 << 32]
    banks = tuple(
        (start, _groups([s for a, s in sectors if start <= a < end])) for start, end in zip(starts, starts[1:])
    )
    return FlashLayout(banks, layout.options, layout.alignment, layout.erased)


# -----------------------------------------------------------------------------
def flash_algorithm(path: Path) -> tuple[int, list[tuple[int, int]], int]:
    """
    Reads the `FlashDevice` description of a CMSIS-Pack flash algorithm (*.FLM).

    :return: the device start address, the (address, size) of every sector and the
             value of an erased byte.
    """
    data = Path(path).read_bytes()
    # ELF32 little endian: find the symbol table and the section containing the symbol
    shoff, shentsize, shnum = struct.unpack_from("<I", data, 0x20)[0], *struct.unpack_from("<HH", data, 0x2E)
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    symtab = next(s for s in sections if s[1] == 2)
    strtab = sections[symtab[6]]
    for offset in range(symtab[4], symtab[4] + symtab[5], 16):
        name, value, _, _, _, shndx = struct.unpack_from("<IIIBBH", data, offset)
        end = data.index(b"\0", strtab[4] + name)
        if data[strtab[4] + name : end] == b"FlashDevice":
            section = sections[shndx]
            device = data[section[4] + value - section[3] :]
            break
    else:
        raise ValueError(f"No FlashDevice in {path}")
    # struct FlashDevice: Vers, DevName[128], DevType, DevAdr, szDev, szPage, Res, valEmpty, ...
    start, size = struct.unpack_from("<II", device, 132)
    erased = device[148]
    sectors, offset = [], 160
    while (entry := struct.unpack_from("<II", device, offset)) != (0xFFFFFFFF, 0xFFFFFFFF):
        sectors.append((entry[1], entry[0]))
        offset += 8
    # Expand the (offset, size) pairs into every sector until the device size
    result = []
    for (offset, sector_size), (end, _) in zip(sectors, sectors[1:] + [(size, 0)]):
        result += [(start + a, sector_size) for a in range(offset, end, sector_size)]
    return start, result, erased


# -----------------------------------------------------------------------------
def _int(value: str) -> int:
    return int(value.strip(), 0)


def _register(value: str) -> int:
    """Register values are hexadecimal, some without prefix, e.g. FLASHSIZE '200' for 512 KB."""
    value = value.strip()
    return int(value, 0 if value[:2].lower() == "0x" else 16)


def _numbers(value: str | None) -> list[int]:
    return [_int(n) for n in re.split(r"[,\s]+", value or "") if n]


@cache
def _database_layouts(die: str) -> list[tuple[int, list[dict], FlashLayout]]:
    """:return: (size, debug interface conditions, layout) of the 'Embedded Flash' configurations."""
    did = die.upper().removeprefix("DIE")
    layouts = []
    for file in sorted(_DB_PATH.glob("STM32_Prog_DB_0x*.xml")):
        if file.stem.upper()[-3:] != did:
            continue
        device = ET.parse(file).getroot().find("Device")
        conditions = {}
        for interface in device.iter("Interface"):
            if interface.get("name") != "JTAG_SWD":
                continue
            for config in interface.findall("Configuration"):
                conditions[_int(config.get("number"))] = {
                    element.tag.lower(): tuple(_register(reg.get(k)) for k in ("address", "mask", "value"))
                    for element in config
                    if (reg := element.find("ReadRegister")) is not None
                }
        for peripheral in device.iter("Peripheral"):
            if peripheral.findtext("Name") != "Embedded Flash":
                continue
            erased = _int(peripheral.findtext("ErasedValue", "0xFF"))
            for config in peripheral.findall("Configuration"):
                # Fields with a condition, e.g. 'NVM-512K', are alternatives for different devices
                fields = [f for bank in config.findall("Bank") for f in bank.findall("Field")]
                variants = list(dict.fromkeys(f.findtext("Condition", "").strip() for f in fields)) or [""]
                for variant in variants:
                    banks = []
                    for bank in config.findall("Bank"):
                        params = [
                            f.find("Parameters")
                            for f in bank.findall("Field")
                            if f.findtext("Condition", "").strip() in ("", variant)
                        ]
                        if params:
                            groups = tuple((_int(f.get("size")), _int(f.get("occurrence"))) for f in params)
                            banks.append((_int(params[0].get("address")), groups))
                    layout = FlashLayout(tuple(banks), (), _int(config.findtext("Alignment", "0")), erased)
                    configs = [conditions.get(n, {}) for n in _numbers(config.get("config"))]
                    layouts.append((layout.size, configs, layout))
    return layouts


def _options(configs: list[dict], size: int) -> set[tuple] | None:
    """
    :return: the bank option bit conditions of the configurations of a flash size, an empty
             set if the layout is valid for any option bits, None if not for this device.
    """
    if not configs:
        return set()
    options = set()
    for conditions in configs:
        # FLASHSIZE counts KB, except on the STM32WB0 and WL3, where it counts words minus one
        sizes = [
            s
            for name, (_, mask, value) in conditions.items()
            if name in _SIZE_CONDITIONS and mask
            for s in ((value & mask) * 1024, ((value & mask) + 1) * 4)
        ]
        if sizes and size not in sizes:
            continue
        # A mask of zero matches any value
        options.add(tuple(sorted((n.upper(), *c) for n, c in conditions.items() if n in _BANK_CONDITIONS and c[1])))
    return options or None


@cache
def flash_layouts(
    die: str, size: int, algorithms: tuple[Path, ...] = (), regions: tuple[tuple[int, int], ...] = ()
) -> tuple[FlashLayout, ...]:
    """
    The organizations of the embedded flash of a device from the STM32CubeProgrammer
    database, one per value of the dual-bank option bits, checked against the CMSIS-Pack
    flash algorithms of the device, which describe one organization each.
    The database only describes the largest flash size of some dies, so smaller devices
    use the first sectors of every bank of the fixed organization of the largest device.

    :param die: the CubeMX die of the device, for example 'DIE413'.
    :param size: the flash size of the device in bytes.
    :param algorithms: paths to the flash algorithms (*.FLM) of the main flash.
    :param regions: (start, size) of the flash banks, if the device has fewer sectors
                    in every bank than the largest device of the die, e.g. STM32H743xG.
    :return: the layouts, empty if neither source describes the device.
    """
    layouts = []
    for layout_size, configs, layout in _database_layouts(die):
        if layout_size == size and (options := _options(configs, size)) is not None:
            options = options or {()}
            layouts += [FlashLayout(layout.banks, o, layout.alignment, layout.erased) for o in sorted(options)]
    if not layouts:
        # Fixed organizations of larger devices, which do not depend on option bits
        fixed = [lay for s, c, lay in _database_layouts(die) if s > size and _options(c, s) in (set(), {()})]
        if fixed:
            largest = max(fixed, key=lambda lay: lay.size)
            if len(regions) < 2:
                start = largest.banks[0][0]
                regions = ((start, size),)
            # Every region keeps the first sectors of the bank at the same address
            sectors = [
                (a, s)
                for a, s in largest.sectors()
                for start, length in regions
                if start <= a and a + s <= start + length
            ]
            if sum(s for _, s in sectors) == size:
                truncated = _with_sectors(largest, sectors)
                banks = tuple(b for b in truncated.banks if b[1])
                layouts.append(FlashLayout(banks, (), largest.alignment, largest.erased))
    # The secure alias of TrustZone devices is described as separate layouts at a higher address
    if layouts:
        start = min(lay.banks[0][0] for lay in layouts)
        layouts = [lay for lay in layouts if lay.banks[0][0] == start]
    layouts = list(dict.fromkeys(layouts))
    if (die.upper(), size) in _ALWAYS_DUAL:
        layouts = [FlashLayout(lay.banks, (), lay.alignment, lay.erased) for lay in layouts if len(lay.banks) > 1]
    # A layout without options is redundant, if layouts with options exist
    if any(lay.options for lay in layouts):
        layouts = [lay for lay in layouts if lay.options]

    checked = set()
    for algorithm in algorithms:
        _, sectors, erased = flash_algorithm(algorithm)
        if sum(s for _, s in sectors) != size:
            continue
        if not layouts:
            layouts.append(FlashLayout(((sectors[0][0], _groups([s for _, s in sectors])),), erased=erased))
        # The algorithm does not know the banks, so it matches the layout with the fewest
        if match := [i for i, lay in enumerate(layouts) if lay.sectors() == sectors]:
            checked.add(min(match, key=lambda i: len(layouts[i].banks)))
            continue
        # The database contains errors, for example, the second bank of the STM32F42x with
        # 1 MB in dual-bank mode, on which the flash algorithm and the reference manual agree.
        addresses = {a for a, _ in sectors}
        # Only the banks of a multi-bank layout can be identified in the algorithm.
        candidates = [
            i
            for i, lay in enumerate(layouts)
            if i not in checked and len(lay.banks) > 1 and all(b in addresses for b, _ in lay.banks)
        ]
        if candidates:
            index = max(candidates, key=lambda i: len(layouts[i].banks))
            LOGGER.warning(f"{die}: {algorithm.name} replaces the sectors of {layouts[index]}")
            layouts[index] = _with_sectors(layouts[index], sectors)
            checked.add(index)
        else:
            LOGGER.warning(f"{die}: {algorithm.name} does not match any organization of the database")
    return tuple(layouts)
