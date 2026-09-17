# Copyright 2025, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# STM32 Memory Map from CMSIS Header

The memory map is reconstructed from the CMSIS device header, which is
compiled and therefore more accurate than the ST SVD files:

1. The peripheral instances and their addresses are the pointer macros, e.g.
   `#define USART1 ((USART_TypeDef *) USART1_BASE)`.
2. The registers are the members of the `*_TypeDef` structure with their
   offset and size computed by the compiler.
3. The bit fields are the `{HEAD}_{REGISTER}_{FIELD}_Pos` and `_Msk` macros.

The difficult part is matching the bit field macros to the structure members,
since the naming is inconsistent between the peripherals and families:

- The head is the peripheral type (`USART_CR1_UE`), the instance
  (`ADC4_SMPR_SMP1`, `OPAMP1_CSR_OPAMP1EN`) or the parent type for
  sub-instances (`DFSDM_CHCFGR1_CHEN` for `DFSDM1_Channel0`).
- Array members use explicit indices (`CAN_F0R1`, `SYSCFG_EXTICR1`), indices
  inside the name (`CAN_TI0R` for `sTxMailBox[0].TIR`) or placeholders
  (`DMA_SxCR`, `SAI_xCR1`, `TSC_IOGXCR`, `GFXMMU_LUTxL`).
- Some macros differ from the member name (`DBGMCU_APB1_FZ` for `APB1FZ`,
  `DCMI_RIS` for `RISR`, `FLASH_CR` for `NSCR` and `SECCR`).

For each member, a list of candidate tokens is generated in order of
preference, and the first one with bit field macros is used together with the
register name derived from it. Instance-specific heads take precedence over
the type heads.

The USB OTG device and host blocks have no instance macros, so they are added
from the `USB_OTG_*_BASE` offset macros.
"""

import os
import re
import bisect
import logging
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field

from . import cubehal
from ..extract import HeaderData, extract_header
from ...svd import Device, Peripheral, Register, BitField, EnumeratedValue
from ...cubehal.registers import folder as cubehal_folder
from ...utils import ext_path

LOGGER = logging.getLogger(__name__)

_HEADER_PATH = ext_path("stmicro/header")
_BLOCK_SIZE = 0x400
# Arrays without bit fields and more elements are described as one register with dimension
_MAX_ARRAY_REGISTERS = 64

# (sub-instance, typedef, base macro, count macros, size macro)
_USB_OTG_BLOCKS = (
    ("Device", "USB_OTG_DeviceTypeDef", "USB_OTG_DEVICE_BASE", None, None),
    (
        "INEndpoint",
        "USB_OTG_INEndpointTypeDef",
        "USB_OTG_IN_ENDPOINT_BASE",
        ("MAX_IN_ENDPOINTS", "EP_NBR"),
        "USB_OTG_EP_REG_SIZE",
    ),
    (
        "OUTEndpoint",
        "USB_OTG_OUTEndpointTypeDef",
        "USB_OTG_OUT_ENDPOINT_BASE",
        ("MAX_OUT_ENDPOINTS", "EP_NBR"),
        "USB_OTG_EP_REG_SIZE",
    ),
    ("Host", "USB_OTG_HostTypeDef", "USB_OTG_HOST_BASE", None, None),
    (
        "HostChannel",
        "USB_OTG_HostChannelTypeDef",
        "USB_OTG_HOST_CHANNEL_BASE",
        ("HOST_MAX_CHANNEL_NBR", "CH_NBR"),
        "USB_OTG_HOST_CHANNEL_SIZE",
    ),
)
_USB_OTG_REGISTERS = (("HPRT", "USB_OTG_HOST_PORT_BASE"), ("PCGCCTL", "USB_OTG_PCGCCTL_BASE"))


@dataclass
class Report:
    """Discrepancies found in the header during the reconstruction."""

    header: str
    defines: int = 0
    """Number of bit field macros defined in the device header."""
    unassigned: list[str] = field(default_factory=list)
    """Bit field macros that are not assigned to any register."""
    empty: list[str] = field(default_factory=list)
    """Registers without any bit field macros."""
    overlapping: list[tuple[str, str, str]] = field(default_factory=list)
    """(register, removed bit field, remaining bit field) with overlapping bits."""
    renamed: list[tuple[str, str]] = field(default_factory=list)
    """(peripheral, register) that were renamed due to name collisions."""
    hinted: list[tuple[str, str]] = field(default_factory=list)
    """(register, bit field macro prefix) that were paired by the CubeHAL source code."""
    restricted: list[tuple[str, str]] = field(default_factory=list)
    """(peripheral, register.field) that are not supported by the instance."""
    alternates: list[str] = field(default_factory=list)
    """Registers that were split into alternate registers due to overlapping bit fields."""
    interrupts: list[str] = field(default_factory=list)
    """Interrupts that could not be assigned to a peripheral."""
    enumerations: int = 0
    """Number of bit fields with enumerated values."""


@dataclass
class _Register:
    name: str
    offset: int
    size: int
    fields: dict[str, tuple[int, int]]
    sub: str | None = None
    dim: int = 0
    type: str = ""
    member: str = ""
    macros: dict[str, str] = field(default_factory=dict)
    """The bit field macro of each bit field."""


def device_headers() -> list[Path]:
    """:return: all STM32 device headers in the CMSIS header repository."""
    headers = []
    for header in sorted(_HEADER_PATH.glob("stm32*xx/Include/stm32*.h")):
        content = header.read_text(encoding="utf-8", errors="replace")
        if "_TypeDef" in content and re.search(r'#include +"core_cm', content):
            headers.append(header)
    return headers


def header_defines(header: Path, core: str = None) -> list[str]:
    """:return: the device define from the family header and the core define for dual-core devices."""
    define = None
    for family in header.parent.glob("stm32*.h"):
        content = family.read_text(encoding="utf-8", errors="replace")
        if match := re.search(rf'#include +"{re.escape(header.name)}"', content):
            # The define is checked right before the include
            lines = content[: match.start()].splitlines()[-3:]
            if defines := re.findall(r"defined *\( *(STM32\w+) *\)", "\n".join(lines)):
                define = defines[-1]
                break
    defines = [define or (header.stem[:9].upper() + header.stem[9:])]
    if "CORE_CM4 or CORE_CM7" in header.read_text(encoding="utf-8", errors="replace"):
        defines.append(f"CORE_{(core or 'cm7').upper()}")
    return defines


def _strip_security(name: str) -> str:
    return re.sub(r"_(NS|S)$", "", name)


class _BitFields:
    """Bit field macros with a fast prefix search."""

    def __init__(self, data: HeaderData):
        self.fields = {}
        self.order = {}
        self.alias = {}
        for name, position in data.values.items():
            if not name.endswith("_Pos") or (mask := data.values.get(f"{name[:-4]}_Msk")) is None:
                continue
            name = name[:-4]
            self.fields[name] = _runs((mask & 0xFFFFFFFF) >> position, position)
            self.order[name] = data.local_macros.get(f"{name}_Pos", len(data.local_macros))
            # Aliases of other bit field macros, e.g. OCTOSPI_CR_EN_Pos XSPI_CR_EN_Pos
            value = data.macros.get(f"{name}_Pos", "").strip("() \t")
            if value.endswith("_Pos") and data.values.get(f"{value[:-4]}_Msk") is not None:
                self.alias[name] = value[:-4]
        self.local = {name for name in self.fields if f"{name}_Pos" in data.local_macros}
        self.names = sorted(self.fields)
        self.assigned = set()

    def _with_prefix(self, prefix: str) -> list[str]:
        index = bisect.bisect_left(self.names, prefix)
        result = []
        while index < len(self.names) and self.names[index].startswith(prefix):
            result.append(self.names[index])
            index += 1
        return result

    def has(self, head: str, token: str) -> bool:
        return f"{head}_{token}" in self.fields or bool(self._with_prefix(f"{head}_{token}_"))

    def take(self, head: str, token: str) -> dict[str, str]:
        """:return: the bit fields of a register by name, which are marked as assigned."""
        prefix = f"{head}_{token}_"
        names = self._with_prefix(prefix)
        if f"{head}_{token}" in self.fields:
            names.append(f"{head}_{token}")
        self.assigned.update(names)
        return {name[len(prefix) :] if name.startswith(prefix) else token: name for name in names}

    def take_prefix(self, prefix: str) -> dict[str, str]:
        """:return: the bit fields of a register with the macro prefix, e.g. `FW_CR`."""
        head, token = prefix.split("_", 1)
        return self.take(head, token)

    def unassigned(self) -> list[str]:
        assigned = set(self.assigned)
        for name, target in self.alias.items():
            if name in assigned or target in assigned:
                assigned.update((name, target))
        return sorted(name for name in self.local if name not in assigned)


def _runs(mask: int, position: int) -> list[tuple[int, int, int]]:
    """:return: the contiguous bit runs of a mask as (position, width, index of first bit)."""
    runs, index = [], 0
    while mask:
        while not mask & 1:
            mask >>= 1
            position += 1
        width = 0
        while mask & 1:
            mask >>= 1
            width += 1
        runs.append((position, width, index))
        position += width
        index += width
    return runs


def _type_heads(typedef: str, instance: str = None) -> list[str]:
    name = re.sub(r"_?TypeDef$", "", typedef)
    parts = name.split("_")
    heads = ["_".join(parts[:i]) for i in range(len(parts), 0, -1)]
    heads += [head.upper() for head in heads if head.upper() != head]
    if instance:
        instance = _strip_security(instance)
        heads.append(instance)
        heads.append(re.sub(r"\d+$", "", instance))
        heads.append(re.sub(r"\d+$", "", instance.split("_")[0]))
    return list(dict.fromkeys(heads))


def _instance_heads(instance: str) -> list[str]:
    """Heads of the instance itself, which are more specific than the type, e.g. ADC4_SMPR or OPAMP1_CSR."""
    if not instance:
        return []
    instance = _strip_security(instance)
    return list(dict.fromkeys([instance, re.sub(r"\d+$", "", instance)]))


def _underscores(token: str) -> list[str]:
    """Variants with an underscore at a digit boundary, e.g. APB1FZ -> APB1_FZ."""
    return [
        token[:p] + "_" + token[p:]
        for p in range(1, len(token))
        if token[p - 1].isdigit() != token[p].isdigit() and "_" not in token[p - 1 : p + 1]
    ]


def _indexed(member: str, index: int) -> list[str]:
    tokens = [f"{member[:-1]}{index}R"] if member.endswith("R") else []
    tokens.append(f"{member}{index}")
    tokens += [member[:p] + str(index) + member[p:] for p in range(1, len(member))]
    return tokens


def _tokens(member: str, index: int = None, subtype: str = None, array_base: int = 0) -> list[tuple[str, str]]:
    """:return: ordered candidates of (register token, kind) for a structure member."""
    m = member
    tokens = [(m, "exact")]
    indices = ()
    if index is not None:
        # Pairs of registers with low and high part, e.g. GFXMMU_LUTxL and GFXMMU_LUTxH
        tokens.append((f"{m}x{'LH'[index % 2]}", "lut"))
        # FSMC and FMC interleave the chip-select and timing registers
        if m in ("BTCR", "BWTR"):
            reg = "BCR" if m == "BTCR" and index % 2 == 0 else "BTR" if m == "BTCR" else "BWTR"
            return [(f"{reg}{index // 2 + 1}", "indexed"), (f"{reg}x", "fsmc")]
        indices = (index, index + 1) if array_base == 0 else (index + 1,)
        for idx in indices:
            tokens += [(token, "indexed") for token in _indexed(m, idx)]
        if index < 2:
            tokens.append((f"{m}{'LH'[index]}", "indexed"))
    tokens += [(m[:p] + "x" + m[p:], "x") for p in range(len(m) + 1)]
    tokens += [(m[:p] + "X" + m[p:], "X") for p in range(len(m) + 1)]
    if subtype and (letter := subtype.split("_")[-1][:1].upper()):
        tokens.append((f"{letter}x{m}", "x"))
        # Register suffix omitted, e.g. DMA_SxNDT for NDTR
        if m.endswith("R"):
            tokens.append((f"{letter}x{m[:-1]}", "xR"))
    tokens.append((re.sub(r"\d+$", "", m), "reduced"))
    tokens.append((re.sub(r"\d", "x", m), "reduced"))
    if "_" in m:
        tokens.append((m.split("_")[0], "reduced"))
    # Member names with placeholder, e.g. HRTIM_TIMCR for TIMxCR
    if "x" in m:
        tokens.append((m.replace("x", ""), "reduced"))
    # Digits inside the name, e.g. ETH_MACL3L4CR for MACL3L4C0R and USB_CHEP for CHEP0R
    tokens += [
        (m[:p] + m[p + 1 :], "reduced") for p in range(1, len(m) - 1) if m[p].isdigit() and not m[p + 1].isdigit()
    ]
    tokens.append((re.sub(r"\d+R$", "", m), "reduced"))
    # Register suffix omitted, e.g. DCMI_RIS for RISR
    if m.endswith("R"):
        tokens.append((m[:-1], "reduced"))
    # Generic definitions for secure and non-secure registers, e.g. FLASH_CR for NSCR and SECCR
    if re.match(r"(NS|SEC)[A-Z]", m):
        tokens.append((re.sub(r"^(NS|SEC)", "", m), "reduced"))
    # Underscores at different positions, e.g. DBGMCU_APB1_FZ and SYSCFG_ITLINE0_SR
    squashed = m.replace("_", "")
    for idx in indices:
        tokens += [(u, "indexed") for token in _indexed(squashed, idx) for u in _underscores(token)]
    tokens += [(u, "reduced") for u in _underscores(squashed)]
    return list(dict.fromkeys((token, kind) for token, kind in tokens if token))


def _abbreviation(members: list[str], sub: str) -> str:
    """:return: the common member prefix that abbreviates the sub-instance, e.g. FLT for Filter or CH for Channel."""
    members = [m for m in members if not m.upper().startswith("RESERVED")]
    if not sub or len(members) < 2:
        return ""
    prefix = os.path.commonprefix(members)
    consonants = "".join(c for c in sub.upper() if c not in "AEIOU")
    if len(prefix) < 2 or prefix[0] != sub[0].upper() or not re.match(".*?".join(prefix), consonants):
        return ""
    return prefix


def _name(kind: str, token: str, member: str, index: int, subindex: str, abbreviation: str) -> str:
    idx = subindex if subindex is not None else index
    if kind == "fsmc":
        return token.replace("x", str(index // 2 + 1), 1)
    if kind == "lut":
        return f"{token[:-2]}{index // 2}{token[-1]}"
    if kind == "indexed":
        return token
    if kind == "X" or (kind == "exact" and "X" in token and index is not None and subindex is None):
        if idx is None:
            return token
        return token.replace("X", str(idx + 1) if subindex is None else str(idx), 1)
    if kind == "x" and idx is not None:
        return token.replace("x", str(idx), 1)
    if kind == "xR" and idx is not None:
        return token.replace("x", str(idx), 1) + "R"
    # The exact or reduced tokens use the member name
    if idx is None:
        return member
    if abbreviation and subindex is not None and member.startswith(abbreviation) and member != abbreviation:
        return f"{abbreviation}{idx}{member[len(abbreviation) :]}"
    if "x" in member:
        return member.replace("x", str(idx), 1)
    # The member name already contains the index, e.g. FSMC_Bank4->PCR4
    if subindex is not None and member.endswith(str(idx)):
        return member
    if kind == "reduced" and index is not None and subindex is None and token == re.sub(r"\d+$", "", member):
        return token
    return f"{member}{idx}"


class _Matcher:
    def __init__(self, data: HeaderData, bitfields: _BitFields, hints: dict = None, report: Report = None):
        self.bitfields = bitfields
        self.hints = hints or {}
        self.report = report
        self.instances = {_strip_security(name) for name in data.instances}
        self.members = {typedef: [m.name for m in members] for typedef, members in data.structs.items()}

    def _heads(self, typedef, instance, parent, sub):
        heads = _instance_heads(sub or instance) + _type_heads(typedef, instance)
        return list(dict.fromkeys(heads + (_type_heads(parent) if parent else [])))

    def _array_base(self, heads, member):
        for head in heads:
            if any(self.bitfields.has(head, token) for token in _indexed(member, 0)):
                return 0
        return 1

    def _match(self, typedef, member, index, subindex, instance, parent, sub):
        abbreviation = ""
        if sub and subindex is not None and not typedef.startswith("USB_OTG"):
            name = re.sub(r"(\d+|[A-Z])$", "", _strip_security(sub).split("_")[-1])
            abbreviation = _abbreviation(self.members.get(typedef, []), name)
        subtype = typedef.replace("_TypeDef", "") if subindex is not None or parent else None
        heads = self._heads(typedef, instance, parent, sub)
        base = self._array_base(heads, member) if index is not None else 0
        tokens = _tokens(member, index, subtype if subtype and "_" in subtype else None, base)
        specific = _instance_heads(sub or instance)
        others = [head for head in heads if head not in specific]
        # Definitions specific to the instance take precedence, e.g. ADC4_SMPR over ADC_SMPR1
        for candidates in (specific, others):
            for token, kind in tokens:
                for head in candidates:
                    if self.bitfields.has(head, token):
                        return _name(kind, token, member, index, subindex, abbreviation), self.bitfields.take(
                            head, token
                        )
        return _name("exact", member, member, index, subindex, abbreviation), {}

    def match(self, typedef, member, index=None, subindex=None, instance=None, parent=None, sub=None):
        """:return: the register name and its bit field macros by field name."""
        name, fields = self._match(typedef, member, index, subindex, instance, parent, sub)
        if not fields and index:
            # Arrays with bit fields only defined for the first element, e.g. CAN_F0R1 for all filter banks
            name0, fields0 = self._match(typedef, member, 0, subindex, instance, parent, sub)
            if fields0:
                if "0" in name0:
                    name = re.sub(r"(?<![0-9])0(?![0-9])", str(index), name0, count=1)
                return name, fields0
        prefix = self.hints.get((typedef, member))
        # Instance specific bit field macros must not be used for other instances, e.g. TIM1_AF1 for TIM6
        head = prefix.split("_")[0] if prefix else None
        if head in self.instances and head != _strip_security(instance or "") and head not in _type_heads(typedef):
            prefix = None
        # Bit field macros of arrays depend on the index, e.g. SYSCFG_ITLINE0_SR for IT_LINE_SR[0]
        if not fields and prefix and index is None:
            # The CubeHAL source code uses these bit field macros with the register
            if self.report is not None:
                self.report.hinted.append((f"{typedef}.{member}", prefix))
            fields = self.bitfields.take_prefix(prefix)
        return name, fields


def _flatten(data: HeaderData, typedef: str, base: int = 0):
    """:return: (typedef, member, array index, offset, size) of all registers in the structure."""
    registers = []
    for member in data.structs.get(typedef, []):
        if member.name.upper().startswith("RESERVED") or (typedef, member.name) not in data.offset:
            continue
        offset = data.offset[(typedef, member.name)] + base
        size = data.size[(typedef, member.name)]
        esize = data.sizeof.get(member.type) or {"uint32_t": 4, "uint16_t": 2, "uint8_t": 1}.get(member.type, size)
        count = size // esize if member.length else 1
        for index in range(count):
            if member.type in data.structs:
                for t, m, idx, o, s in _flatten(data, member.type, offset + index * esize):
                    registers.append((t, m, index if member.length else idx, o, s))
            # The odd elements of the FSMC and FMC write timing registers are reserved
            elif member.name == "BWTR" and index % 2:
                continue
            else:
                registers.append(
                    (typedef, member.name, index if member.length else None, offset + index * esize, esize)
                )
    return registers


def _usb_otg_blocks(data: HeaderData, instances):
    """The USB OTG device and host blocks have no instance macros, only offset macros."""
    values = data.values
    blocks, registers = [], []
    for name, typedef, address in instances:
        if typedef != "USB_OTG_GlobalTypeDef":
            continue
        base_name = _strip_security(name)
        for sub, subtype, base, counts, size in _USB_OTG_BLOCKS:
            if subtype not in data.structs or base not in values:
                continue
            if counts is None:
                blocks.append((f"{name}_{sub}", subtype, address + values[base], name))
                continue
            count = next((values[f"{base_name}_{c}"] for c in counts if f"{base_name}_{c}" in values), None)
            if count is None:
                count = (values["USB_OTG_OUT_ENDPOINT_BASE"] - values["USB_OTG_IN_ENDPOINT_BASE"]) // values[size]
            for index in range(count):
                blocks.append((f"{name}_{sub}{index}", subtype, address + values[base] + index * values[size], name))
        for register, base in _USB_OTG_REGISTERS:
            if base in values:
                registers.append((name, register, values[base]))
    return blocks, registers


def _group_instances(instances, usb_blocks):
    """:return: the parent of all sub-instances and the addresses of virtual parents."""
    names = {name for name, _, _ in instances}
    parents = {name: parent for name, _, _, parent in usb_blocks}
    for name, _, _ in instances:
        base, suffix = _strip_security(name), name[len(_strip_security(name)) :]
        parts = base.split("_")
        for i in range(len(parts) - 1, 0, -1):
            if (parent := "_".join(parts[:i]) + suffix) in names:
                parents[name] = parent
                break
    # Sub-instances without a common parent instance in the same address block, e.g. DFSDM1_Channel0
    groups = defaultdict(list)
    for name, _, address in instances:
        base, suffix = _strip_security(name), name[len(_strip_security(name)) :]
        match = re.fullmatch(r"(\w+?\d*)_([A-Za-z]{3,})(\d+)", base)
        if name not in parents and match and match.group(1) + suffix not in names:
            groups[match.group(1) + suffix].append((name, address))
    virtual = {}
    for parent, members in groups.items():
        addresses = [address for _, address in members]
        if len(members) > 1 and min(addresses) // _BLOCK_SIZE == max(addresses) // _BLOCK_SIZE:
            virtual[parent] = min(addresses)
            for name, _ in members:
                parents[name] = parent
    return parents, virtual


def _bit_fields(register: _Register, fields: dict[str, str], bitfields: _BitFields, report: Report) -> list[dict]:
    """
    Splits non-contiguous bit fields and removes overlapping bit fields.

    :return: the bit fields of alternate registers by bit field name, e.g. the
             input capture bit fields of TIM_CCMR1 that overlap the output compare bit fields.
    """
    candidates = []
    for name, macro in fields.items():
        for position, width, index in bitfields.fields[macro]:
            split = len(bitfields.fields[macro]) > 1
            fname = f"{name}_{index}" if index else name
            candidates.append((fname, position, width, macro, split, name))

    def bits(position, width):
        return set(range(position, position + width))

    # Remove values and bits of other bit fields, e.g. AFIO_EXTICR1_EXTI0_PB in AFIO_EXTICR1_EXTI0. The parent
    # may be one part of a non-contiguous bit field, e.g. CRYP_CR_ALGOMODE_AES_KEY in bits 5:3 of CRYP_CR_ALGOMODE,
    # which also uses bit 19, otherwise the values replace the bit field itself.
    result = []
    for candidate in candidates:
        cbits = bits(*candidate[1:3])
        parent = next(
            (
                c
                for c in candidates
                if c is not candidate
                and candidate[0] != c[0]
                and candidate[0].startswith(c[5] + "_")
                and cbits <= bits(*c[1:3])
            ),
            None,
        )
        if parent is not None:
            report.overlapping.append((register.name, candidate[0], parent[0]))
            continue
        result.append(candidate)
    candidates = result
    # Remove masks of multiple bit fields, e.g. EXTI_IMR1_IM for all EXTI_IMR1_IMx
    result = []
    for candidate in candidates:
        cbits = bits(*candidate[1:3])
        base = re.sub(r"(_ALL|\d+)$", "", candidate[0])
        others = [c for c in candidates if c is not candidate and bits(*c[1:3]) < cbits]
        covered = set().union(*(bits(*c[1:3]) for c in others)) if others else set()
        # A mask of many bit fields or of bit fields with the same name, e.g. EXTI_IMR_IM for EXTI_IMR_MRx
        similar = all(c[0].startswith(base) for c in others)
        if len(others) > 1 and covered == cbits and (similar or len(others) >= 8):
            report.overlapping.append((register.name, candidate[0], others[0][0]))
            continue
        result.append(candidate)
    # Prefer non-alias and non-split bit fields in the order of definition
    result.sort(key=lambda c: (c[3] in bitfields.alias, c[4], bitfields.order.get(c[3], 0), c[1]))
    used, alternate = {}, []
    for fname, position, width, macro, split, _name in result:
        overlap = next((used[b] for b in bits(position, width) if b in used), None)
        if overlap is not None and fname not in register.fields:
            # A narrow field at the edge of a much wider field, e.g. TIM_CNT_UIFCPY in TIM_CNT_CNT
            opos, owidth = register.fields[overlap]
            cbits, obits = bits(position, width), bits(opos, owidth)
            if cbits < obits and owidth > 2 * width and (position == opos or position + width == opos + owidth):
                trimmed = sorted(obits - cbits)
                register.fields[overlap] = (trimmed[0], len(trimmed))
                report.overlapping.append((register.name, overlap, fname))
                overlap = None
        if overlap is not None or fname in register.fields:
            # Bit fields with a different layout are an alternate function of the register
            inside = any(
                bits(position, width) <= bits(*register.fields[n])
                for n in {used[b] for b in bits(position, width) if b in used}
            )
            # Bit fields inside another bit field are values, e.g. I2C_OAR2_OA2MASK01 in I2C_OAR2_OA2MSK
            if overlap is not None and not split and macro not in bitfields.alias and not inside:
                if register.fields[overlap] != (position, width):
                    alternate.append((fname, position, width, macro))
                    continue
            report.overlapping.append((register.name, fname, overlap or fname))
            continue
        register.fields[fname] = (position, width)
        register.macros[fname] = macro
        used.update((b, fname) for b in bits(position, width))

    # Build alternate registers from the overlapping bit fields
    layers = []
    while alternate:
        layer, used, remaining = {}, {}, []
        for fname, position, width, macro in alternate:
            if any(b in used for b in bits(position, width)) or fname in layer:
                remaining.append((fname, position, width, macro))
                continue
            layer[fname] = (position, width, macro)
            used.update((b, fname) for b in bits(position, width))
        # Bit fields of the register that do not overlap and do not belong to an overlapping bit field
        conflicting = {re.sub(r"_\d+$", "", n) for n, (p, w) in register.fields.items() if bits(p, w) & set(used)}
        for fname, (position, width) in register.fields.items():
            if not bits(position, width) & set(used) and re.sub(r"_\d+$", "", fname) not in conflicting:
                layer.setdefault(fname, (position, width, register.macros[fname]))
        layers.append(layer)
        alternate = remaining
    return layers


def _interrupts(data: HeaderData, names: set[str], report: Report) -> dict[str, list[tuple[str, int, str]]]:
    """:return: the (name, number, description) of the interrupts by peripheral name."""
    interrupts = defaultdict(list)
    for irq, number in sorted(data.interrupts.items(), key=lambda i: i[1]):
        if number < 0:
            continue
        parts = irq.split("_")
        tokens = {"_".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 1, len(parts) + 1)}
        matches = {n for n in names if n in tokens or any(n.endswith(f"_{token}") for token in tokens if "_" in token)}
        if not matches:
            # Numbered interrupt lines, e.g. EXTI0 or EXTI9_5 for EXTI
            matches = {n for n in names if n in {re.sub(r"\d+$", "", token) for token in tokens}}
        if not matches:
            # Shared interrupts of numbered instances, e.g. ADC for ADC1 and ADC2
            matches = {n for n in names if re.fullmatch(r"[A-Z]+\d+", n) and re.sub(r"\d+$", "", n) in tokens}
        # Do not assign to both the sub-instance and its parent, e.g. DFSDM1_FLT0 to DFSDM1
        matches = {n for n in matches if not any(m != n and m.startswith(n) for m in matches)}
        description = data.macro_descriptions.get(f"{irq}_IRQn", "")
        for match in matches:
            interrupts[match].append((irq, number, description))
        if not matches:
            report.interrupts.append(irq)
    return interrupts


def _channels(data: HeaderData) -> dict[int, set[str]]:
    """:return: the timer instances by capture/compare channel, e.g. IS_TIM_CC3_INSTANCE."""
    channels = {}
    for macro, identifiers in data.instance_macros.items():
        if match := re.fullmatch(r"IS_TIM_CC(\d)_INSTANCE", macro):
            channels[int(match.group(1))] = {_strip_security(i) for i in identifiers}
    return channels


def _restrict(pname: str, registers: list[_Register], restrictions, counters, channels, report: Report):
    """Removes the bit fields and registers that are not supported by the instance."""
    instance = _strip_security(pname)
    for register in list(registers):
        if not register.fields:
            continue
        for fname, macro in list(register.macros.items()):
            keys = [(register.type, register.member, macro), (register.type, register.member, fname)]
            # The capture/compare channel bit fields of timers, e.g. TIM_CCER_CC3E or TIM_CCR3
            channel = None
            if register.type == "TIM_TypeDef":
                if match := re.match(r"(?:(?:CC|OC|IC)(\d)(?!\d)|OIS(\d)(?!\d))", fname):
                    channel = int(match.group(1) or match.group(2))
                elif match := re.fullmatch(r"CCR(\d)", register.member):
                    channel = int(match.group(1))
            if channel in channels and instance not in channels[channel]:
                keys.append(None)
            if None in keys or any(key in restrictions and instance not in restrictions[key] for key in keys):
                report.restricted.append((pname, f"{register.name}.{fname}"))
                del register.fields[fname]
                del register.macros[fname]
                continue
            position, width = register.fields[fname]
            # Instances without 32-bit counter only have 16-bit fields
            if width > 16 and any(key in counters and instance not in counters[key] for key in keys):
                report.restricted.append((pname, f"{register.name}.{fname}[{position + 15}:{position}]"))
                register.fields[fname] = (position, 16)
        if not register.fields:
            registers.remove(register)


def memory_map(data: HeaderData, name: str = None, cubehal_path: Path = None) -> tuple[Device, Report]:
    """
    Reconstructs the memory map of a CMSIS header.

    :param data: the extracted header data.
    :param name: the name of the device, defaults to the header name.
    :param cubehal_path: the CubeHAL folder of the family for additional information.
    :return: the memory map as SVD device tree and a report of discrepancies.
    """
    report = Report(data.header.name)
    bitfields = _BitFields(data)
    hints, restrictions, counters, enumerations = {}, {}, {}, {}
    if cubehal_path is not None:
        hints = cubehal.register_hints(data, bitfields, cubehal_path)
        restrictions, counters = cubehal.instance_restrictions(data, bitfields, cubehal_path)
        enumerations = cubehal.enumerations(data.header, data, bitfields, cubehal_path)
    matcher = _Matcher(data, bitfields, hints, report)

    instances, seen = [], defaultdict(list)
    for iname, (typedef, address) in sorted(data.instances.items(), key=lambda i: (len(i[0]), i[0])):
        typedef = data.aliases.get(typedef, typedef)
        if not typedef.endswith("TypeDef") or typedef not in data.structs:
            continue
        # Sub-instances of the same type at the same address are aliases, e.g. ETH_MAC for ETH
        base = _strip_security(iname)
        if any(base.startswith(_strip_security(other) + "_") for other in seen[(typedef, address)]):
            LOGGER.debug(f"Ignoring {iname} as alias of {seen[(typedef, address)]}")
            continue
        seen[(typedef, address)].append(iname)
        instances.append((iname, typedef, address))
    usb_blocks, usb_registers = _usb_otg_blocks(data, instances)
    instances += [(iname, typedef, address) for iname, typedef, address, _ in usb_blocks]
    parents, virtual = _group_instances(instances, usb_blocks)

    peripherals = {}
    for iname, typedef, address in instances:
        if iname not in parents:
            peripherals[iname] = (address, typedef, [])
    for pname, address in virtual.items():
        peripherals[pname] = (address, "", [])

    # Sub-instances with letter index, e.g. HRTIM1_TIMA to HRTIM1_TIMF, but not HRTIM1_COMMON
    siblings = defaultdict(set)
    for iname, parent in parents.items():
        sub = _strip_security(iname)[len(_strip_security(parent)) + 1 :]
        siblings[(parent, sub[:-1])].add(sub)

    for iname, typedef, address in sorted(instances, key=lambda i: i[2]):
        pname = parents.get(iname, iname)
        paddress, ptype, registers = peripherals[pname]
        sub, subindex = None, None
        if iname in parents:
            sub = _strip_security(iname)[len(_strip_security(pname)) + 1 :]
            if match := re.search(r"\d+$", sub):
                subindex = match.group(0)
            elif re.search(r"[A-Z]$", sub) and len(siblings[(pname, sub[:-1])]) > 1:
                subindex = sub[-1]
        arrays = defaultdict(list)
        for rtype, member, index, offset, size in _flatten(data, typedef, address - paddress):
            parent = (ptype or None) if iname in parents else None
            rname, fields = matcher.match(rtype, member, index, subindex, pname, parent, iname)
            register = _Register(rname, offset, size, {}, sub, type=rtype, member=member)
            layers = _bit_fields(register, fields, bitfields, report)
            registers.append(register)
            if layers:
                report.alternates.append(f"{pname}.{rname}")
            for number, layer in enumerate(layers, start=1):
                alternate = _Register(
                    f"{rname}_ALT{number if len(layers) > 1 else ''}", offset, size, {}, sub, type=rtype, member=member
                )
                alternate.fields = {fname: (p, w) for fname, (p, w, _) in layer.items()}
                alternate.macros = {fname: m for fname, (_, _, m) in layer.items()}
                registers.append(alternate)
            if index is not None:
                arrays[(rtype, member)].append(register)
        # Large arrays without bit fields are usually memories, e.g. PKA_RAM
        for (rtype, member), elements in arrays.items():
            if len(elements) > _MAX_ARRAY_REGISTERS and not any(r.fields for r in elements):
                for register in elements:
                    registers.remove(register)
                rname = f"{sub}_{member}" if subindex is None and sub else member
                registers.append(_Register(rname, elements[0].offset, elements[0].size, {}, sub, len(elements), rtype))
    for pname, member, offset in usb_registers:
        rname, fields = matcher.match("USB_OTG_TypeDef", member, instance=pname)
        register = _Register(rname, offset, 4, {}, member=member)
        _bit_fields(register, fields, bitfields, report)
        peripherals[pname][2].append(register)

    for address, ptype, registers in peripherals.values():
        # Sub-instances take precedence over the same nested structures of the parent, e.g. HRTIM1_TIMA,
        # otherwise the parent registers take precedence, e.g. EXTI_C2IMR1 over EXTI_D2
        subs = {(register.offset, register.type) for register in registers if register.sub is not None}
        parent = {
            register.offset
            for register in registers
            if register.sub is None and (register.offset, register.type) not in subs
        }
        registers[:] = [
            r
            for r in registers
            if (r.sub is None and (r.offset, r.type) not in subs) or (r.sub is not None and r.offset not in parent)
        ]

    channels = _channels(data)
    for pname, (address, ptype, registers) in peripherals.items():
        _restrict(pname, registers, restrictions, counters, channels, report)
    interrupts = _interrupts(data, {re.sub(r"_NS$", "", pname) for pname in peripherals}, report)

    device = Device(name or data.header.stem, compatible=data.defines[:1])
    access = {(typedef, member.name): member.access for typedef, members in data.structs.items() for member in members}
    signatures, addresses = {}, {}
    for pname, (address, ptype, registers) in sorted(peripherals.items(), key=lambda p: (p[1][0], p[0])):
        # Registers of sub-instances with colliding names are prefixed with the sub-instance name
        names = defaultdict(set)
        for register in registers:
            names[register.name].add(register.sub)
        colliding = {sub for subs in names.values() if len(subs) > 1 for sub in subs if sub is not None}
        for register in registers:
            if register.sub in colliding:
                report.renamed.append((pname, register.name))
                register.name = f"{register.sub}_{register.name}"
        registers.sort(key=lambda r: (r.offset, r.name))
        for register in registers:
            if not register.fields:
                report.empty.append(f"{pname}.{register.name}")

        # Instances with identical registers are derived from the first instance
        signature = (ptype, tuple((r.name, r.offset, r.size, tuple(sorted(r.fields.items()))) for r in registers))
        derived = signatures.setdefault(signature, pname) if ptype and registers else pname
        # The non-secure instances use the plain name
        peripheral = Peripheral(re.sub(r"_NS$", "", pname), ptype, address, parent=device)
        peripheral.description = data.type_descriptions.get(ptype, "")
        if ptype:
            peripheral.group = re.sub(r"_?TypeDef$", "", ptype)
        peripheral.interrupts = interrupts.get(peripheral.name, [])
        if (alternate := addresses.setdefault(address, peripheral.name)) != peripheral.name:
            peripheral.alternate = alternate
        if derived != pname:
            peripheral.derived_from = re.sub(r"_NS$", "", derived)
            continue
        offsets = {}
        for register in registers:
            treg = Register(register.name, register.offset, register.size, parent=peripheral)
            treg.access = access.get((register.type, register.member), "read-write")
            treg.description = data.member_descriptions.get((register.type, register.member), "")
            if register.dim:
                treg.dim = register.dim
            # Registers at the same offset are alternates, e.g. unions
            if (alternate := offsets.setdefault(register.offset, register.name)) != register.name:
                treg.alternate = alternate
            for fname, (position, width) in sorted(register.fields.items(), key=lambda f: f[1]):
                tfield = BitField(fname, position, width, parent=treg)
                macro = register.macros.get(fname, "")
                tfield.description = data.macro_descriptions.get(macro, "")
                values = enumerations.get(bitfields.alias.get(macro, macro), [])
                if values and all(value < (1 << width) for _, value, _ in values):
                    report.enumerations += 1
                    for vname, value, description in values:
                        EnumeratedValue(vname, value, description=description, parent=tfield)

    report.defines = len(bitfields.local)
    report.unassigned = bitfields.unassigned()
    return device, report


def memory_map_from_header(header: Path, core: str = None) -> tuple[Device, Report]:
    """
    Extracts the header data and reconstructs the memory map of a CMSIS header.

    :param header: path to the CMSIS device header.
    :param core: the core of dual-core devices, `cm4` or `cm7`.
    :return: the memory map as SVD device tree and a report of discrepancies.
    """
    data = extract_header(header, header_defines(header, core))
    name = header.stem + (f"_{core}" if core else "")
    return memory_map(data, name, cubehal_folder(header.parent.parent.name))
