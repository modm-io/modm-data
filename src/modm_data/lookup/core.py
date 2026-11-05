"""Cortex-M core peripherals (SCB, NVIC, SysTick, MPU, FPU, DWT, ITM, CoreDebug) from the CMSIS core headers.

The device SVDs only have the vendor peripherals, these registers are added from `core_cmX.h` in the same form,
so that the SVD tools can also decode fault registers like SCB.CFSR read from a board.
"""
import re
from functools import cache

from .headers import MODM_DATA, _evaluate_define, _macros

CORE = MODM_DATA / "ext/arm/cmsis/CMSIS/Core/Include"
MEMBER = re.compile(r"(?:__IM|__OM|__IOM|__I|__O|__IO)\s+(uint8_t|uint16_t|uint32_t)\s+(\w+)(?:\[(\w+)\])?\s*;"
                    r"\s*/\*!<\s*Offset:\s*(0x[0-9A-Fa-f]+)\s*\(([^)]*)\)\s*(.*?)\s*\*/")


@cache
def peripherals(core: str) -> dict[str, dict]:
    """The core peripherals of 'cortex-m4' etc. in the form of svd._load"""
    path = CORE / f"core_{core.replace('cortex-', 'c')}.h"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    defines = _macros(text)
    structs = {}
    for body, name in re.findall(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+_Type)\s*;", text, re.S):
        registers = []
        for ctype, member, count, offset, access, description in MEMBER.findall(body):
            if member.upper().startswith("RESERVED"):
                continue
            size = {"uint8_t": 1, "uint16_t": 2, "uint32_t": 4}[ctype]
            number = _evaluate_define(count, {**defines, count: re.sub(r"[Uu]$", "", count)}) if count else None
            if number and member in ("IP", "IPR", "SHP", "SHPR"):
                # priorities are word registers in the PM, NVIC_IPRx with IRQ 4x..4x+3 and SCB_SHPR1..3 with the
                # exceptions from 4, also where CMSIS declares bytes or starts at SHPR2 (Cortex-M0+)
                label, first = ("SHPR", (int(offset, 16) - 0x18) // 4 + 1) if member.startswith("SHP") else ("IPR", 0)
                for index in range(first, first + number * size // 4):
                    registers.append({"name": f"{label}{index}", "base": None,
                                      "offset": int(offset, 16) + (index - first) * 4, "size": 32,
                                      "description": description.strip(), "alternate": "", "access": access.strip(),
                                      "fields": [{"name": f"PRI_{4 * index + k}", "offset": 8 * k, "width": 8,
                                                  "description": "", "values": {}} for k in (3, 2, 1, 0)]})
                continue
            for index in range(number or 1):
                registers.append({"name": f"{member}{index}" if number else member, "base": member,
                                  "offset": int(offset, 16) + index * size, "size": size * 8,
                                  "description": description.strip(), "alternate": "", "access": access.strip()})
        structs[name] = registers
    result = {}
    for instance, typedef, base in re.findall(r"^\s*#\s*define\s+(\w+)\s+\(\(\s*(\w+_Type)\s*\*\s*\)\s*(\w+)\s*\)", text, re.M):
        if typedef not in structs or (address := _evaluate_define(base, defines)) is None or instance in result:
            continue
        # the field macros are named after the type, also for aliases like SCB_NS and CoreDebug
        prefix = typedef.removesuffix("_Type")
        # the MPU aliases RBAR_A1 etc. have the fields of RBAR
        registers = [r if r["base"] is None else {**r, "fields": _fields(prefix, r["base"], defines) or
                                                  _fields(prefix, re.sub(r"_A\d+$", "", r["base"]), defines)}
                     for r in structs[typedef]]
        result[instance] = {"base": address, "description": f"Cortex-M core: {typedef.removesuffix('_Type')}",
                            "registers": registers}
    return result


def _fields(instance: str, register: str, defines: dict[str, str]) -> list[dict]:
    prefix = f"{instance}_{register}_"
    fields = []
    for name in defines:
        if not (name.startswith(prefix) and name.endswith("_Pos")):
            continue
        position = _evaluate_define(name, defines)
        mask = _evaluate_define(name[:-4] + "_Msk", defines)
        if position is None or not mask:
            continue
        fields.append({"name": name[len(prefix):-4], "offset": position, "width": bin(mask).count("1"),
                       "description": "", "values": {}})
    # masks of a group of fields, e.g. SCB_CFSR_USGFAULTSR for the UFSR bits, are left out when the bits are there
    bits = lambda f: set(range(f["offset"], f["offset"] + f["width"]))
    fields = [f for f in fields if not any(g is not f and bits(g) < bits(f) for g in fields)]
    # aliases at the same bits, e.g. ENDIANESS of ENDIANNESS or VECTKEYSTAT of VECTKEY: the first definition wins
    fields = list({(f["offset"], f["width"]): f for f in reversed(fields)}.values())
    return sorted(fields, key=lambda f: -f["offset"])
