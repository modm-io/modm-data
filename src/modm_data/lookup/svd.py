"""STM32 register layouts from the SVD files that modm-data generates from the CMSIS headers (header2svd).

They follow the names of the headers, so they agree with what modm code uses. They have no reset values and
no access types, the reference manual has those (see docs.doc_register).
"""
import re
from functools import cache
from pathlib import Path

from ..svd import read_svd
from . import core as _core
from .headers import MODM_DATA, _identifiers, _select, cores as core_types, normalize

SVD = MODM_DATA / "ext/stmicro/svd"


@cache
def _load(path: Path) -> dict[str, dict]:
    """peripheral -> {base, description, registers: [{name, offset, size, description, fields}]}"""
    peripherals = {}
    for p in read_svd(path).children:
        registers = []
        for r in p.children:
            fields = [{"name": f.name, "offset": f.position, "width": f.width, "description": f.description or "",
                       "values": {int(v.value, 0): (v.name, v.description or "") for v in f.children}}
                      for f in r.children]
            registers.append({"name": r.name, "offset": r.offset, "size": 8 * r.width, "description": r.description or "",
                              "alternate": r.alternate or "", "fields": sorted(fields, key=lambda f: -f["offset"])})
        peripherals[p.name] = {"base": p.address, "description": p.description or "",
                               "registers": sorted(registers, key=lambda r: r["offset"])}
    return peripherals


def _device(device: str) -> tuple[str, dict]:
    """The SVD of exactly one header, dual-core devices need the core, e.g. 'stm32h745zi@m7'"""
    device = normalize(device)
    core = device.split("@")[1] if "@" in device else ""
    found = _select(device)
    if len(found) != 1:
        names = ", ".join(h.stem for h in found) or "none"
        raise ValueError(f"'{device}' must select exactly one device header, it selects: {names}")
    header, parts = next(iter(found.items()))
    cores = sorted({_identifiers()[p].core for p in parts if getattr(_identifiers()[p], "core", "")})
    # only some dual-core devices have one header per core (H745), others share one (WL55)
    path = SVD / f"header_{header.stem}.svd"
    if cores and (SVD / f"header_{header.stem}_c{cores[0]}.svd").exists():
        if not core:
            raise ValueError(f"{header.stem} is a dual-core device, add the core: "
                             f"{', '.join(f'stm32{device}@{c}' for c in cores)}")
        path = SVD / f"header_{header.stem}_c{core}.svd"
    if not path.exists():
        raise FileNotFoundError(f"no SVD {path.name} generated from the headers")
    # the Cortex-M core peripherals of the device (of the given core), the vendor peripherals take precedence
    kinds = {core_types()[p] for p in parts if not core or p.endswith(f"@{core}")}
    kind = kinds.pop() if len(kinds) == 1 else ""
    label = path.stem.removeprefix("header_")
    if len(kinds) > 1:
        # dual-core devices with one header (WL55) have the vendor peripherals without a core, but not the core ones
        label += (f" (dual-core, add the core for core registers: "
                  f"{', '.join(f'stm32{device}@{c}' for c in cores)})")
    if not parts and (m := re.search(r'#include\s*["<]core_(c\w+?)\.h', header.read_text(errors="replace"))):
        # a header that devices.db does not know yet names its core itself
        kind = m[1].replace("c", "cortex-", 1)
    return label, {**_core.peripherals(kind), **_load(path)}


def _bits(f) -> str:
    return f"{f['offset'] + f['width'] - 1}:{f['offset']}" if f["width"] > 1 else str(f["offset"])


def _find(peripherals: dict, register: str) -> list[tuple[str, dict]]:
    """Registers by 'RCC.CR', 'RCC_CR', 'GPIOA_MODER' or an address inside them"""
    if re.fullmatch(r"(0x)?[0-9a-fA-F_]+", register) and register.lower().startswith("0x"):
        address = int(register.replace("_", ""), 16)
        return [(p, r) for p, info in peripherals.items() for r in info["registers"]
                if info["base"] + r["offset"] <= address < info["base"] + r["offset"] + r["size"] // 8]
    name = register.upper().replace(".", "_")
    return [(p, r) for p, info in peripherals.items() for r in info["registers"]
            if f"{p}_{r['name']}".upper() == name]


def register_map(device: str, peripheral: str) -> str:
    """All registers of a peripheral instance ('RCC', 'GPIOA', 'TIM1') with address, offset and bit fields,
    from the SVD generated from the CMSIS header of the device. Register arrays are shown once."""
    name, peripherals = _device(device)
    if not peripheral.strip():
        raise ValueError(f"no peripheral given, e.g. 'RCC', see device_header for the device")
    key = next((k for k in peripherals if k.upper() == peripheral.upper()), None)
    if key is None:
        raise ValueError(f"no peripheral {peripheral} in {name}: {' '.join(sorted(peripherals))}")
    info = peripherals[key]
    lines = [f"{name} {key} @ 0x{info['base']:08X}: {info['description']}"]
    fields = lambda r: " ".join(f"{f['name']}[{_bits(f)}]" for f in r["fields"])
    registers = info["registers"]
    i = 0
    while i < len(registers):
        r = registers[i]
        # an array: consecutive registers with the same fields, names that only differ in numbers, the same stride
        j = i + 1
        while j < len(registers) and fields(registers[j]) == fields(r) and \
                re.sub(r"\d+", "#", registers[j]["name"]) == re.sub(r"\d+", "#", r["name"]) and \
                registers[j]["offset"] - registers[j - 1]["offset"] == (registers[i + 1]["offset"] - r["offset"]):
            j += 1
        alternate = f" (alternate view of {r['alternate']})" if r["alternate"] else ""
        description = f": {r['description']}" if r["description"] else ""
        if j - i > 2:
            last, stride = registers[j - 1], registers[i + 1]["offset"] - r["offset"]
            lines.append(f"0x{info['base'] + r['offset']:08X} +0x{r['offset']:03X}..0x{last['offset']:03X} "
                         f"{r['name']}..{last['name']} ({j - i} registers, stride {stride}){description}")
        else:
            j = i + 1
            lines.append(f"0x{info['base'] + r['offset']:08X} +0x{r['offset']:03X} {r['name']}{description}{alternate}")
        if r["fields"]:
            lines.append("  " + fields(r))
        i = j
    return "\n".join(lines)


def _values(value: str) -> list[tuple[int | None, int]]:
    """(address, value) pairs: OpenOCD mdw output ('0x40021000: 03030500 00004a83'), or one number.
    Bare digits are hex like OpenOCD prints them, '0x', '0b' and '0d' prefixes are respected."""
    lines = [m for line in value.splitlines() if (m := re.match(r"\s*(0x[0-9a-fA-F]+)\s*:\s*((?:[0-9a-fA-F]+\s*)+)$", line))]
    if lines:
        # other lines of the openocd output, e.g. warnings, are ignored
        return [(int(m[1], 16) + 4 * i, int(w, 16)) for m in lines for i, w in enumerate(m[2].split())]
    token = value.strip().replace("_", "").lower()
    try:
        number = int(token[2:], 10) if token.startswith("0d") else int(token, 0) if token[:2] in ("0x", "0b") else int(token, 16)
    except ValueError:
        raise ValueError(f"'{value.strip()[:80]}' is neither a number nor mdw output with lines like '0x40021000: 03030500'")
    if not 0 <= number < 1 << 32:
        raise ValueError(f"{value.strip()} does not fit into a 32-bit register")
    return [(None, number)]


def decode_register(device: str, register: str = "", value: str = "") -> str:
    """Bit fields of a register given as 'RCC.CR', 'RCC_CR' or an address like '0x40021000'.
    With `value`, every field is decoded with its enumerated value names, and set bits outside of all fields are
    reported. `value` may be the output of OpenOCD's mdw (several lines and words, then `register` can be empty);
    bare numbers are hex like mdw prints them."""
    name, peripherals = _device(device)
    pairs = _values(value) if value else [(None, None)]
    out, dump = [], len(pairs) > 1
    for address, raw in pairs:
        target = f"0x{address:08X}" if address is not None else register
        if not target:
            raise ValueError("give a register or a value with an address like mdw prints it")
        found = _find(peripherals, target)
        if not found:
            out.append(f"no register at {target} in {name}" if address is not None else "")
            if address is None:
                # e.g. SCB.CFSR on a Cortex-M0+, which has no configurable faults
                head, _, rest = register.strip().replace(".", "_").partition("_")
                if info := next((v for k, v in peripherals.items() if k.upper() == head.upper()), None):
                    raise ValueError(f"{head} of {name} has no register {rest}, it has: "
                                     f"{' '.join(r['name'] for r in info['registers'])}")
                raise ValueError(f"no register {register} in {name}, see register_map")
            continue
        for peripheral, r in found:
            start = peripherals[peripheral]["base"] + r["offset"]
            head = f"{peripheral}.{r['name']} @ 0x{start:08X}" + (f": {r['description']}" if r["description"] else "")
            if target.lower().startswith("0x") and int(target, 16) != start:
                head += f" (0x{int(target, 16):08X} is not the start of the register, +{int(target, 16) - start})"
            if raw is not None:
                head += f" = 0x{raw:08X}"
            out.append(head)
            if dump and not raw:
                continue
            covered, hidden = 0, 0
            for f in r["fields"]:
                mask = ((1 << f["width"]) - 1) << f["offset"]
                covered |= mask
                line = f"  {_bits(f):>5} {f['name']}"
                if raw is not None:
                    v = (raw & mask) >> f["offset"]
                    # only the fields that are set, and zeros that have a name, e.g. PLLP = 0 DIV2
                    if not v and 0 not in f["values"]:
                        hidden += 1
                        continue
                    enum = f["values"].get(v)
                    line += f" = {v:#x}" + (f" {enum[0]}" if enum else "")
                if f["description"]:
                    line += f": {f['description']}"
                if raw is None and f["values"]:
                    line += " (" + ", ".join(f"{k}={n}" for k, (n, _) in sorted(f["values"].items())) + ")"
                out.append(line)
            if raw is not None and r["fields"] and raw & ~covered & ((1 << r["size"]) - 1):
                out.append(f"  bits outside of all fields are set: 0x{raw & ~covered:08X}")
            if hidden:
                out.append(f"  ({hidden} other {'field is' if hidden == 1 else 'fields are'} 0)")
    return "\n".join(l for l in out if l)
