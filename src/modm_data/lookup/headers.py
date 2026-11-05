"""STM32 CMSIS device headers and the devices that use them, from modm-data."""
import json, os, re, sqlite3
from collections import defaultdict
from functools import cache
from pathlib import Path
from types import SimpleNamespace

from ..utils import root_path

# the checkout this package is in, or another one with the same ext folder
MODM_DATA = Path(os.environ.get("MODM_DATA", root_path(".")))
DB = MODM_DATA / "ext/modm-data/devices.db"
HEADERS = MODM_DATA / "ext/stmicro/header"


@cache
def _devices() -> sqlite3.Connection:
    """devices.db, read-only and shared by all calls (and the threads of the MCP server)"""
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)


@cache
def _headers() -> dict[Path, list[str]]:
    """Maps every STM32 device header to the part names that include it."""
    db = _devices()
    rows = db.execute("SELECT d.partname, c.header, c.define FROM device d JOIN core c ON c.id = d.core_id "
                      "WHERE d.platform = 'stm32' ORDER BY d.partname")
    result = {}
    for part, family, define in rows:
        # same folder naming as modm_data.header2svd.stmicro.Header
        folder = family if "xx" in family else family + "x"
        result.setdefault(HEADERS / folder / "Include" / f"{define.lower()}.h", []).append(part)
    # headers of families that devices.db does not know yet (C5, G5, WBA, ...) have no part names
    for header in sorted(HEADERS.glob("stm32*xx/Include/stm32*.h")):
        if re.fullmatch(r"stm32\w+xx", header.stem) and header.stem != header.parent.parent.name:
            result.setdefault(header, [])
    return result


def _unknown(header: Path) -> str:
    return f"{header} (not in devices.db, so no part names)"


@cache
def cores() -> dict[str, str]:
    """The Cortex-M core of every STM32 part name, e.g. 'cortex-m4'"""
    db = _devices()
    return dict(db.execute("SELECT d.partname, c.type FROM device d JOIN core c ON c.id = d.core_id WHERE d.platform = 'stm32'"))


@cache
def _identifiers() -> dict[str, SimpleNamespace]:
    """The lbuild `target` of every STM32 part name: family, name, pin, size, package, temperature, variant."""
    db = _devices()
    rows = db.execute("SELECT partname, identifier FROM device WHERE platform = 'stm32'")
    return {part: SimpleNamespace(**json.loads(identifier)) for part, identifier in rows}


@cache
def _defines(header: Path) -> frozenset[str]:
    text = header.read_text(encoding="utf-8", errors="replace")
    return frozenset(re.findall(r"^\s*#\s*define\s+(\w+)", text, re.M))


def normalize(device: str, empty: bool = False) -> str:
    """A device prefix in one form for all tools: ' STM32G474RET6TR ' is 'g474ret6', '@m7' cores are kept.
    An empty device is refused unless `empty` allows all devices."""
    name, _, core = device.strip().lower().replace(" ", "").partition("@")
    # lbuild targets name the silicon revision, e.g. stm32h743zit6/revy
    name = name.split("/")[0].removesuffix(".h").removeprefix("stm32")
    # the packing of the ordering code (TR tape and reel) is not part of the device
    name = re.sub(r"(?<=\d)(tr|x)$", "", name) if len(name) > 7 else name
    if not name and not empty:
        raise ValueError("no device given, e.g. 'stm32g474re'")
    return name + (f"@{core}" if core else "")


def _select(device: str, empty: bool = False) -> dict[Path, list[str]]:
    device = normalize(device, empty).split("@")[0]
    # a header name like stm32g474xx selects that header
    if found := {h: parts for h, parts in _headers().items() if h.stem == f"stm32{device}"}:
        return found
    found = {h: [p for p in parts if p.removeprefix("stm32").startswith(device)]
             for h, parts in _headers().items() if any(p.removeprefix("stm32").startswith(device) for p in parts)}
    # headers without part names match by their name, the x are wildcards: 'c5' and 'c552ze' select stm32c552xx
    found |= {h: [] for h, parts in _headers().items() if not parts and
              all(a == b or b == "x" for a, b in zip(device, h.stem.removeprefix("stm32"))) and
              (len(device) >= 2 or not device)}
    if not found and not empty:
        import difflib
        names = sorted({p.split("@")[0] for parts in _headers().values() for p in parts})
        close = difflib.get_close_matches(f"stm32{device}", names, n=5, cutoff=0.6)
        raise ValueError(f"no STM32 device matches '{device}'" + (f", similar: {' '.join(close)}" if close else ""))
    return found


def device_header(device: str) -> str:
    """Header file(s) of all devices whose part name starts with `device`, e.g. 'stm32g474re' or 'h745'."""
    found = _select(device)
    return "\n".join(f"{h} ({len(parts)} devices: {_short(parts)})" if parts else _unknown(h) for h, parts in found.items())


def header_devices(header: str) -> str:
    """All devices that use a device header, given as file name ('stm32g474xx.h') or define ('STM32G474xx')."""
    if not header.strip():
        raise ValueError("no header given, e.g. 'stm32g474xx.h'")
    name = header.lower().removesuffix(".h") + ".h"
    for h, parts in _headers().items():
        if h.name == name:
            return f"{h}\n{len(parts)} devices: {' '.join(parts)}" if parts else _unknown(h)
    return f"no header {name} exists"


def find_define(pattern: str, devices: str = "", limit: int = 20) -> str:
    """Which device headers define a macro matching the regex `pattern` (fullmatch), and which do not.
    `devices` limits the search to part names starting with it ('g4', 'stm32h7', ...). `limit` caps the macro
    names shown per header (0 shows none). Without `devices` and with many headers, the answer is per family."""
    regex = re.compile(pattern)
    have, lack = [], []
    for h, parts in _select(devices, empty=True).items():
        names = sorted(n for n in _defines(h) if regex.fullmatch(n))
        (have if names else lack).append((h, parts, names))
    lines = [f"{len(have)} headers ({sum(len(p) for _, p, _ in have)} devices) define it, "
             f"{len(lack)} headers ({sum(len(p) for _, p, _ in lack)} devices) do not."]
    shown = lambda names: " ".join(names[:limit]) + (f" ... (+{len(names) - limit})" if len(names) > limit else "")
    if len(have) + len(lack) > 30:
        # per family, the headers that lack it are the interesting part
        families = defaultdict(lambda: ([], []))
        for h, parts, names in have + lack:
            families[h.parent.parent.name.removesuffix("xx").removesuffix("x")][0 if names else 1].append(h)
        for family, (yes, no) in sorted(families.items()):
            if yes:
                missing = f", not in: {' '.join(h.stem for h in no)}" if no else ""
                lines.append(f"{family}: {len(yes)} of {len(yes) + len(no)} headers{missing}")
        absent = sorted(f for f, (yes, _) in families.items() if not yes)
        if absent:
            lines.append(f"not in any header of: {' '.join(absent)}")
        names = sorted({n for _, _, ns in have for n in ns})
        if names and limit > 0:
            lines.append(f"macros: {shown(names)}")
        return "\n".join(lines + ["(use devices='g4' etc. for the headers of one family)"])
    if have:
        lines.append("\nDefined in:")
        common = have[0][2] if all(names == have[0][2] for _, _, names in have) else None
        if common and limit > 0:
            lines.append(f"all of them: {shown(common)}")
        if common and not lack:
            lines.append(" ".join(h.name for h, _, _ in have))
        else:
            for h, parts, names in have:
                lines.append(f"{h.name} [{_short(parts)}]" + ("" if common or limit <= 0 else f": {shown(names)}"))
    if lack:
        lines.append("\nNot defined in:")
        lines += [f"{h.name} [{_short(parts)}]" for h, parts, _ in lack]
    return "\n".join(lines)


def device_condition(pattern: str, family: str = "", condition: str = "") -> str:
    """lbuild template condition for the devices whose header defines a macro matching the regex `pattern`
    (fullmatch), e.g. `%% set with_register = target.name in ["b0", "b1"]`, one per family.
    With `condition`, checks that existing condition instead and lists the devices it gets wrong."""
    # also accept the output of this tool
    condition = re.sub(r"^\s*(stm32\w+ \([^)]*\):\s*)?%%\s*set\s+\w+\s*=\s*", "", condition.strip())
    if condition:
        _check(condition)
    if condition and not family:
        raise ValueError("checking a condition needs the family it is meant for, e.g. family='g0'")
    regex, ids = re.compile(pattern), _identifiers()
    matched = {p for h, parts in _headers().items() if any(regex.fullmatch(n) for n in _defines(h)) for p in parts}
    families = defaultdict(list)
    if family:
        # the header folder, also for families that devices.db folds into another one (WBA into WB)
        fam = normalize(family)
        families[fam] = [p for h, parts in _headers().items() if h.parent.parent.name.rstrip("x") == f"stm32{fam}"
                         for p in parts]
    else:
        for part, target in ids.items():
            families[target.family].append(part)
    if family and not families[fam]:
        known = [h for h in _headers() if h.parent.parent.name.removeprefix("stm32").startswith(fam)]
        raise ValueError(f"family {fam} has no devices in devices.db" +
                         (", its headers exist, but conditions need the part names" if known else ""))
    lines = []
    for fam in sorted(families):
        universe, selected = families[fam], [p for p in families[fam] if p in matched]
        if not family and not selected:
            continue
        cond = condition or _condition([ids[p] for p in selected], [ids[p] for p in universe])
        result = set(_evaluate(cond, universe))
        lines.append(f"stm32{fam} ({len(selected)} of {len(universe)} devices): %% set with_register = {cond}")
        if missing := sorted(set(selected) - result):
            lines.append(f"  missing: {_by_name(missing)}")
        if extra := sorted(result - set(selected)):
            lines.append(f"  wrongly included: {_by_name(extra)}")
    return "\n".join(lines) or f"'{pattern}' is not defined in any header" + (f" of stm32{fam}" if family else "")


def _by_name(parts: list[str]) -> str:
    """Devices grouped by target.name, e.g. 'c1 (29 devices), b1 size e (4 devices)'"""
    ids, groups = _identifiers(), defaultdict(list)
    for p in parts:
        groups[ids[p].name].append(p)
    return ", ".join(f'name "{n}" ({len(ps)} devices: {_short(ps, 3)})' for n, ps in sorted(groups.items()))


def _check(condition: str):
    """Only comparisons of target attributes with constants, combined with and, or, not"""
    import ast
    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid condition: {e.msg}")
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.Compare, ast.Eq, ast.NotEq,
               ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Constant, ast.List, ast.Tuple, ast.Load)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "target" \
                and not node.attr.startswith("_"):
            continue
        if isinstance(node, ast.Name) and node.id == "target" or isinstance(node, allowed):
            continue
        raise ValueError(f"invalid condition: only target.<attribute> compared with constants, not "
                         f"'{ast.get_source_segment(condition, node) or type(node).__name__}'")


def _evaluate(condition: str, parts: list[str]) -> list[str]:
    ids = _identifiers()
    return [p for p in parts if eval(condition, {"__builtins__": {}}, {"target": ids[p]})]


def _attribute_in(attribute: str, values) -> str:
    values = sorted(set(values))
    if len(values) == 1:
        return f'target.{attribute} == "{values[0]}"'
    return f'target.{attribute} in [{", ".join(f"{chr(34)}{v}{chr(34)}" for v in values)}]'


def _attribute_not_in(attribute: str, values) -> str:
    return _attribute_in(attribute, values).replace(" == ", " != ").replace(" in [", " not in [")


def _name_expression(selected: set[str], universe: list[str]) -> str:
    """Shortest of: name list, `name >= threshold`, list plus threshold, `name not in` list"""
    universe = sorted(set(universe))
    if selected == set(universe):
        return "True"
    candidates = [_attribute_in("name", selected)]
    for threshold in universe:
        above = {n for n in universe if n >= threshold}
        below = {n for n in selected if n < threshold}
        if above == selected:
            candidates.append(f'target.name >= "{threshold}"')
        elif below and below | above == selected:
            candidates.append(f'{_attribute_in("name", below)} or target.name >= "{threshold}"')
    candidates.append(_attribute_not_in("name", set(universe) - selected))
    return min(candidates, key=len)


def _condition(selected: list[SimpleNamespace], universe: list[SimpleNamespace]) -> str:
    if not selected:
        return "False"
    if len(selected) == len(universe):
        return "True"
    by_name = defaultdict(list)
    for target in universe:
        by_name[target.name].append(target)
    chosen = {id(t) for t in selected}
    full, partial = set(), defaultdict(list)
    for name, targets in by_name.items():
        hits = [t for t in targets if id(t) in chosen]
        if len(hits) == len(targets):
            full.add(name)
        elif hits:
            # the first attribute that separates the devices of this name, headers mostly differ by size
            for attribute in ("size", "pin", "variant", "package", "temperature"):
                values = {getattr(t, attribute) for t in hits}
                if all((getattr(t, attribute) in values) == (id(t) in chosen) for t in targets):
                    partial[(attribute, tuple(sorted(values)))].append(name)
                    break
            else:
                partial[None] += [t for t in hits]
    clauses = [_name_expression(full, list(by_name))] if full else []
    for key, names in sorted(partial.items(), key=lambda kv: str(kv[0])):
        if key is None:
            clauses += [" and ".join(_attribute_in(a, [getattr(t, a)]) for a in
                        ("name", "pin", "size", "package", "temperature", "variant")) for t in names]
        else:
            attribute, values = key
            others = {getattr(t, attribute) for n in names for t in by_name[n]} - set(values)
            within = min(_attribute_in(attribute, values), _attribute_not_in(attribute, others), key=len)
            clauses.append(f"({_attribute_in('name', names)} and {within})")
    return clauses[0] if len(clauses) == 1 else " or ".join(f"({c})" if " or " in c else c for c in clauses)


def peripheral_buses(device: str) -> str:
    """Which bus (AHB1, APB2, ...) every peripheral of a device is on, from the `*_BASE` defines of its header."""
    found = _select(device)
    lines = []
    for header, parts in found.items():
        lines.append(f"{header.name} [{_short(parts) or 'not in devices.db'}]")
        for bus, peripherals in _bus_map(header.read_text(encoding="utf-8", errors="replace")).items():
            # numbered sub-instances like DMA channels are one entry per controller
            grouped = {}
            for p in peripherals:
                grouped.setdefault(re.sub(r"(_[A-Za-z]*)\d+$", r"\1*", p.split(" ")[0]), []).append(p)
            number = lambda v: int(re.search(r"(\d+)$", v.split(" ")[0])[1])
            names = [k.replace("*", f"{min(map(number, vs))}..{max(map(number, vs))}") if len(vs) > 1 else vs[0]
                     for k, vs in grouped.items()]
            lines.append(f"  {bus} ({len(names)}): {', '.join(names)}")
    return "\n".join(lines)


# the domains of the STM32H7 use their own bus names
_DOMAINS = {("D1", "AHB1"): "AHB3", ("D1", "APB1"): "APB3", ("D3", "AHB1"): "AHB4", ("D3", "APB1"): "APB4"}
_MEMORY = re.compile(r"RAM|FLASH|_BB$|UID|PACKAGE|OTP|SIZE|ROM|SYSMEM|ENGI|ICP")


def _macros(text: str) -> dict[str, str]:
    """All object-like macros of a header with their value, without comments and casts"""
    defines = {}
    for name, value in re.findall(r"^\s*#define\s+(\w+)[ \t]+([^\n]*)", text, re.M):
        value = re.sub(r"/\*.*?\*/|//.*", "", value)
        value = re.sub(r"\(\s*(?:u?int\d+_t|unsigned long|uint32_t)\s*\)", "", value).strip()
        if value:
            defines[name] = value
    return defines


def _evaluate_define(name: str, defines: dict[str, str], depth: int = 0) -> int | None:
    """Value of an address macro like (PERIPH_BASE + 0x00010000UL) or a plain alias of another one"""
    if depth > 10 or name not in defines:
        return None
    # C literals: 0x hex, 0 octal (MPU_RBAR_XN_Msk is 01UL), decimal
    expr = re.sub(r"\b(0x[0-9A-Fa-f]+|\d+)[UuLl]*\b",
                  lambda m: str(int(m[1], 0) if m[1][:2].lower() == "0x" else int(m[1], 8 if m[1][0] == "0" else 10)),
                  defines[name])
    for ref in set(re.findall(r"\b[A-Za-z_]\w*", expr)):
        if (value := _evaluate_define(ref, defines, depth + 1)) is None:
            return None
        expr = re.sub(rf"\b{ref}\b", str(value), expr)
    try:
        return int(eval(expr, {"__builtins__": {}}))
    except Exception:
        return None


def _bus_name(define: str) -> str | None:
    """AHB2 for AHB2PERIPH_BASE, APB4 for the H7 D3_APB1PERIPH_BASE, None for other defines"""
    if m := re.fullmatch(r"(?:(D[123]|CD|SRD)_)?(AHB|APB)(\d*)PERIPH_BASE(?:_NS|_S)?", define):
        return _DOMAINS.get((m[1], f"{m[2]}{m[3]}"), f"{m[2]}{m[3]}")
    return None


def _bus_of(name: str, defines: dict[str, str], depth: int = 0) -> str | None:
    """The bus define that an address macro is derived from, e.g. GPIOA_BASE -> IOPORT_BASE -> AHB2PERIPH_BASE"""
    if (bus := _bus_name(name)) or depth > 10 or name not in defines:
        return bus
    for ref in re.findall(r"\b[A-Za-z_]\w*", defines[name]):
        if bus := _bus_of(ref, defines, depth + 1):
            return bus
    # the GPIOs of Cortex-M0+ devices are on the single-cycle IOPORT, unless it is derived from an AHB (WB)
    return "IOPORT" if re.fullmatch(r"IOPORT_BASE(?:_NS|_S)?", name) else None


def _bus_map(text: str) -> dict[str, list[str]]:
    """The peripherals of a header (its pointer macros like ((GPIO_TypeDef *) GPIOA_BASE)) by the bus their base
    address is derived from, or by the bus whose address range contains them. Nothing is dropped: peripherals that
    fit no bus are listed as "not mapped"."""
    defines = _macros(text)
    buses = {}
    for name in defines:
        if (bus := _bus_name(name)) and (value := _evaluate_define(name, defines)) is not None:
            buses[bus] = min(buses.get(bus, value), value)
    pointers = re.findall(r"^\s*#define\s+(\w+)\s+\(\(\s*\w+\s*\*\s*\)\s*(\w+)\s*\)", text, re.M)
    names = {p for p, _ in pointers}
    result, used, aliases = defaultdict(set), set(), 0
    for name, base in pointers:
        # TrustZone devices define every peripheral twice (CRC_NS and CRC_S), only the non-secure one is shown
        if name.endswith("_S") and ({name[:-2], f"{name[:-2]}_NS"} & names):
            aliases += 1
            continue
        if name.endswith("_NS") and name[:-3] in names:
            continue
        name = re.sub(r"_NS$", "", name)
        used.add(base)
        address = _evaluate_define(base, defines)
        if bus := _bus_of(base, defines):
            result[bus].add(name)
        elif address is not None and (bus := max((b for b, a in buses.items() if a <= address < a + 0x10000000),
                                                 key=buses.get, default=None)) and name != "DBGMCU":
            result[bus].add(f"{name} 0x{address:08X}")
        elif address is not None:
            result["other address"].add(f"{name} 0x{address:08X}")
        else:
            result["not mapped"].add(name)
    # memories are no peripherals, with their secure alias on TrustZone devices, and do not start at a peripheral
    at = {_evaluate_define(base, defines) for _, base in pointers}
    memories = defaultdict(dict)
    for name, value in defines.items():
        # but not the registers of a memory controller, e.g. FLASH_R or RAMCFG_SRAM1
        if (m := re.fullmatch(r"(\w+)_BASE(_NS|_S)?", name)) and _MEMORY.search(m[1]) and name not in used \
                and not m[1].endswith("_R"):
            if (address := _evaluate_define(name, defines)) is not None and address >= 0x10000 and address not in at:
                memories[m[1]][m[2] or ""] = address
    for name, addresses in memories.items():
        address = addresses.get("", addresses.get("_NS", addresses.get("_S")))
        secure = addresses.get("_S")
        result["memories"].add(f"{name} 0x{address:08X}" + (f" (secure 0x{secure:08X})" if secure and secure != address else ""))
    if aliases:
        result["secure aliases"].add(f"{aliases} peripherals also as <name>_S at their secure address")
    order = sorted((b for b in result if re.fullmatch(r"(AHB|APB)\d*", b)), key=lambda b: (b[:3], int(b[3:] or -1)))
    return {bus: sorted(result[bus]) for bus in [*order, "IOPORT", "other address", "memories", "not mapped",
                                                 "secure aliases"] if result.get(bus)}


def flash_latency(device: str) -> str:
    """Flash wait states of a device: the highest HCLK frequency for each number of wait states, per supply or
    core voltage (VOS range, in mV) from devices.db. Devices with the same table are listed together."""
    device = normalize(device).split("@")[0]
    db = _devices()
    rows = db.execute("SELECT d.partname, p.extra FROM device d JOIN peripheral p ON p.set_id = d.peripheral_set_id "
                      "WHERE d.platform = 'stm32' AND d.partname LIKE ? AND p.name = 'flash' ORDER BY d.partname",
                      (f"stm32{device}%",)).fetchall()
    if not rows:
        _select(device)
        raise ValueError(f"no flash peripheral for 'stm32{device}' in devices.db")
    tables = defaultdict(list)
    for part, extra in rows:
        tables[json.dumps(json.loads(extra).get("latency"), sort_keys=True)].append(part)
    lines = []
    for table, parts in tables.items():
        lines.append(f"{len(parts)} device{'s' * (len(parts) != 1)}: {_short(parts)}")
        if (latency := json.loads(table)) is None:
            lines.append("  no wait state table in devices.db, see find_tables('wait states', device=...)")
            continue
        if not latency:
            lines.append("  no internal flash, so no wait states")
        for mv, limits in sorted(latency.items(), key=lambda kv: -int(kv[0])):
            ws = ", ".join(f"{n} WS <= {f / 1e6:g} MHz" for n, f in enumerate(limits))
            lines.append(f"  {int(mv) / 1000:g} V: {ws}")
    return "\n".join(lines)


def _short(parts: list[str], limit: int = 6) -> str:
    return " ".join(parts[:limit]) + (f" ... (+{len(parts) - limit})" if len(parts) > limit else "")
