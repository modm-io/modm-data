# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Compares the register maps of the sensor datasheets with the ST vendor drivers
https://github.com/STMicroelectronics/STMems_Standard_C_drivers (with submodules).

The driver headers have the register addresses and the bit fields as C structs.
The enums of the driver API are mapped to the fields through the `*_set()`
functions in the driver source, which assign the enum value to a field, for
example `ctrl1_xl.odr_xl = (uint8_t)val & 0x0FU;`.

    make ext/stmicro/STMems_Standard_C_drivers
    python test/sensor_driver_check.py ext/stmicro/STMems_Standard_C_drivers [-v]
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from modm_data.html.stmicro import DatasheetSensor
from modm_data.html2svd.stmicro.__main__ import _sensor_datasheets


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\[\d+(:\d+)?\]", "", name.lower()))


def parse_header(path: Path):
    """-> registers {NAME: (addr, {field: (lsb, width)})}, enums {type: {member: value}}."""
    text = path.read_text(errors="replace")
    prefix = path.name[: -len("_reg.h")].upper().replace("-", "_")
    registers, structs = {}, {}
    for m in re.finditer(r"typedef\s+struct\s*\{(.*?)\}\s*(\w+)_t\s*;", text, re.DOTALL):
        fields, position = {}, 0
        for _, name, width in re.findall(r"uint(8|16|32)_t\s+(\w+)\s*:\s*(\d+)", re.split(r"#elif", m.group(1))[0]):
            if not name.startswith("not_used"):
                fields[name] = (position, int(width))
            position += int(width)
        structs[m.group(2)] = fields
    for m in re.finditer(rf"#define\s+{re.escape(prefix)}_(\w+)\s+(0x[0-9A-Fa-f]+)U?\b", text):
        name, address = m.group(1), int(m.group(2), 16)
        if address > 0xFFFF or "I2C_ADD" in name or name in ("ID",) or name.endswith("_ID"):
            continue
        struct = structs.get(f"{prefix.lower()}_{name.lower()}", {})
        registers[name] = (address, struct)
    enums = {}
    for m in re.finditer(r"typedef\s+enum\s*\{(.*?)\}\s*(\w+)\s*;", text, re.DOTALL):
        members = {}
        for name, value in re.findall(r"(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)U?", m.group(1)):
            members[name] = int(value, 0)
        enums[m.group(2)] = members
    return registers, enums, prefix


def parse_setters(path: Path, enums: dict, prefix: str):
    """-> [(enum type, register name, field, function of the enum value)]."""
    text = path.read_text(errors="replace")
    result = []
    for m in re.finditer(r"int32_t\s+\w+_set\s*\(\s*[^,]+,\s*(\w+)\s+(\w+)\s*\)\s*\{(.*?)\n\}", text, re.DOTALL):
        etype, param, body = m.groups()
        if etype not in enums:
            continue
        names = {param} | set(re.findall(rf"\b{etype}\s+(\w+)\s*=\s*{param}\s*;", body))
        variables = {v: t for t, v in re.findall(rf"\b({prefix.lower()}_\w+)_t\s+(\w+)\s*(?:=\s*\{{0\}})?\s*;", body)}
        for var, field, rhs in re.findall(r"\b(\w+)\.(\w+)\s*=\s*([^;]+);", body):
            if var not in variables or not any(re.search(rf"\b{n}\b", rhs) for n in names):
                continue
            expr = re.sub(r"\(\s*(uint\d+_t|int\d+_t)\s*\)", "", rhs)
            expr = re.sub(r"\b(0x[0-9A-Fa-f]+|\d+)U\b", r"\1", expr)
            for n in names:
                expr = re.sub(rf"\b{n}\b", "VAL", expr)
            if not re.fullmatch(r"[\sVAL0-9a-fA-Fx&|<>()~+\-*]+", expr):
                continue
            register = variables[var][len(prefix) + 1 :].upper()
            result.append((etype, register, field, expr))
    return result


_UNITS = {"hz": "hz", "g": "g", "dps": "dps", "s": "s", "lsb": "lsb"}
_SCALE = {"k": 1e3, "m": 1e-3, "u": 1e-6}


def quantities(text: str) -> dict[str, set[float]]:
    """Numbers with units: 12Hz5 -> 12.5 hz, 1.66 kHz -> 1660 hz, ±2 g, 250dps, ODR/4 -> 4 div."""
    result = defaultdict(set)
    text = text.replace("_", " ")
    text = text.replace("μ", "u").replace("µ", "u")
    pattern = r"(\d+(?:\.\d+)?)(?:([kmu])(\d+))?\s*([kmu]?)(hz|dps|g|s|lsb)(\d*)(?![a-z])"
    for a, scale1, b1, scale, unit, b in re.findall(pattern, text, re.IGNORECASE):
        # 12Hz5 = 12.5 Hz, 1k66Hz = 1.66 kHz, 17KHz2 = 17.2 kHz
        b, scale = b1 or b, scale1 or scale
        value = float(f"{a}.{b}" if b and "." not in a else a)
        unit, scale = unit.lower(), scale.lower()
        if unit == "g" and scale == "m":
            unit, scale = "mg", ""
        result[_UNITS.get(unit, unit)].add(value * _SCALE.get(scale, 1))
    for d in re.findall(r"(?:odr\s*/\s*|div\s*)(\d+)", text, re.IGNORECASE):
        result["div"].add(float(d))
    return result


def same_meaning(vendor: str, html: str):
    """True or False if both name a quantity of the same unit, otherwise None."""
    qv, qh = quantities(vendor), quantities(html)
    units = set(qv) & set(qh)
    if not units:
        return None
    return any(abs(a - b) <= 0.02 * max(a, b) for u in units for a in qv[u] for b in qh[u])


def matches(value: int, pattern: str) -> bool:
    bits = format(value, f"0{len(pattern)}b")
    return len(bits) == len(pattern) and all(p in ("x", b) for p, b in zip(pattern, bits))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("drivers", type=Path)
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args()

    headers = {norm(p.name[: -len("_reg.h")]): p for p in args.drivers.glob("*_STdC/driver/*_reg.h")}
    docs = {norm(d.part): d for d in _sensor_datasheets()}
    total = Counter()
    layout_cases = defaultdict(list)
    enum_cases = defaultdict(list)
    for key in sorted(set(headers) & set(docs)):
        doc: DatasheetSensor = docs[key]
        hregs, enums, prefix = parse_header(headers[key])
        setters = parse_setters(headers[key].with_suffix(".c"), enums, prefix)
        xregs = {}
        for registers in doc.register_map.values():
            for name, register in registers.items():
                xregs.setdefault(norm(name), register)
        c = Counter()
        hnames = {norm(n): n for n in hregs}
        common = set(hnames) & set(xregs)
        c["registers"] = len(common)
        c["only_driver"] = len(set(hnames) - set(xregs))
        c["only_doc"] = len(set(xregs) - set(hnames))
        for k in common:
            address, hfields = hregs[hnames[k]]
            register = xregs[k]
            c["address_eq"] += (address & 0xFF) == (register.address & 0xFF)
            xfields = {f.name: (f.position, f.width) for f in register.fields}
            if hfields and not xfields:
                c["fields_only_driver"] += 1
                layout_cases["fields only in driver"].append((doc.part, register.name, hfields, register.cells))
                continue
            if not hfields or not xfields:
                continue
            c["fields_both"] += 1
            hset, xset = set(hfields.values()), set(xfields.values())
            if hset == xset:
                c["layout_eq"] += 1
                c["names_eq"] += {norm(n) for n in hfields} == {norm(n) for n in xfields}
                continue
            hbits = {p + i for p, w in hset for i in range(w)}
            xbits = {p + i for p, w in xset for i in range(w)}
            hedges = {p for p, _ in hset} | {p + w for p, w in hset}
            xedges = {p for p, _ in xset} | {p + w for p, w in xset}
            if hbits != xbits:
                case = "different bits"
            elif hedges < xedges:
                case = "driver merges fields"
            elif xedges < hedges:
                case = "driver splits field"
            else:
                case = "different split"
            layout_cases[case].append((doc.part, register.name, hfields, xfields))
        # Enums of the driver API
        for etype, rname, fname, expr in setters:
            if norm(rname) not in xregs or rname not in hregs or fname not in hregs[rname][1]:
                continue
            register = xregs[norm(rname)]
            position, width = hregs[rname][1][fname]
            members = enums[etype]
            c["enum_fields"] += 1
            field = next((f for f in register.fields if (f.position, f.width) == (position, width)), None)
            if field is None:
                c["enum_field_layout_differs"] += 1
                enum_cases["field layout differs"].append((doc.part, rname, fname, etype))
                continue
            if not field.values:
                c["enum_no_doc_values"] += 1
                enum_cases["no values in datasheet"].append((doc.part, rname, field.name, etype, field.description))
                continue
            c["enum_doc_values"] += 1
            single = sum(1 for e, *_ in setters if e == etype) == 1
            missing = []
            for member, value in members.items():
                try:
                    fvalue = eval(expr, {}, {"VAL": value}) & ((1 << width) - 1)
                except (SyntaxError, TypeError, NameError):
                    continue
                c["enum_members"] += 1
                doc_value = next((v for v in field.values if matches(fvalue, v.value)), None)
                if doc_value is None:
                    c["enum_member_no_value"] += 1
                    missing.append((member, fvalue))
                    continue
                if not single:
                    c["enum_member_composite"] += 1  # the enum sets several fields, its name is not about this one
                    continue
                meaning = same_meaning(member[len(prefix) + 1 :], doc_value.name + " " + (doc_value.description or ""))
                c[f"enum_member_meaning_{meaning}"] += 1
                if meaning is False:
                    enum_cases["different meaning"].append(
                        (doc.part, rname, field.name, member, fvalue, doc_value.name)
                    )
            if missing:
                enum_cases["value not in datasheet"].append(
                    (doc.part, rname, field.name, missing, [v.value for v in field.values])
                )
        total += c
        if args.verbose:
            print(f"{doc.part:14}", dict(c))
    print("Parts:", len(set(headers) & set(docs)))
    for k, v in sorted(total.items()):
        print(f"  {k:30} {v}")
    for title, cases in list(layout_cases.items()) + list(enum_cases.items()):
        print(f"\n## {title}: {len(cases)}")
        for case in cases[: 6 if args.verbose < 2 else None]:
            print("  ", *case)


if __name__ == "__main__":
    sys.exit(main())
