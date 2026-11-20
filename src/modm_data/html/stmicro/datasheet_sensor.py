# Copyright 2022, 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
Register maps of the STMicro sensor datasheets.

The register chapters have the same structure in almost all sensor datasheets:

- A register address map table per register bank with the columns Name, Type,
  Address (Hex, Binary), Default and Comment.
- A section per register, for example `9.12 CTRL1_XL (10h)`, with a bit layout
  table (one cell per bit, MSB first), a field description table (`ODR_XL[3:0]`
  and text) and value tables of the multi-bit fields.

The bit layout table names every bit (`FS1_XL`, `FS0_XL`), so the bits are
merged into fields by the names in the description table (`FS[1:0]_XL`).
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dcfield
from functools import cached_property

from ..document import Document
from ..table import Table
from ..text import Heading, Text

LOGGER = logging.getLogger(__name__)

_NAME = r"[A-Za-z][\w/]*"
# NAME (10h), NAME register (address 10h), NAME (10h - R/W), NAME [Interrupt CTRL register] (22h)
_REG_HEADING = re.compile(
    rf"({_NAME})\s*(?:\[[^\]]*\]\s*)?(?:register\s*)?\(\s*(?:address\s*)?([0-9A-Fa-f]{{2,4}})h?\b[^)]*\)"
)
# Cells without a field: constant bits, reserved bits, dashes
_CONSTANT = re.compile(r"^[01x]?$|^(?i:reserved|res\d*|not used|n\.?a\.?)$|^[- ]+$")
_BITS = re.compile(r"^[01x]+$")


@dataclass
class SensorEnum:
    value: str
    """Binary value MSB first, `x` is a don't care bit."""
    name: str
    description: str = None


@dataclass
class SensorField:
    name: str
    position: int
    width: int
    description: str = None
    values: list[SensorEnum] = dcfield(default_factory=list)


@dataclass
class SensorRegister:
    name: str
    address: int = None
    access: str = None
    reset: int = None
    size: int = 8
    description: str = None
    cells: list[str] = None
    fields: list[SensorField] = dcfield(default_factory=list)


def _superscript(match) -> str:
    """Footnote markers `(1)` and trademarks `SM` are removed, I²C and exponents `2^-10` are kept."""
    text = match.group(1).replace("−", "-").replace("–", "-")
    if text == "2" and match.string[match.start() - 1 : match.start()] == "I":
        return "²"
    return "^" + text if re.fullmatch(r"-?\d+", text) else ""


def _clean(text) -> str:
    if text is None:
        return ""
    text = text.html if isinstance(text, Text) else text
    text = re.sub(r"<sup>(.*?)</sup>", _superscript, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _rows(table: Table) -> list[list[str]]:
    return [[_clean(table.cell(x, y)) for x in range(table.columns)] for y in range(table.rows)]


def _norm(name: str) -> str:
    """Field name for comparisons: FS[1:0]_XL, FS_XL and fs_xl are equal."""
    name = re.sub(r"\[\d+(:\d+)?\]", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _range_name(name: str):
    """`FS[1:0]_XL` -> (`FS`, 1, 0, `_XL`), also with spaces `FTYPE [2:0]`."""
    if m := re.fullmatch(r"(.*?)\s*\[(\d+):(\d+)\]\s*(.*)", name.replace(" ", "")):
        return m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    return None


def _bank_name(caption: str) -> str:
    caption = re.sub(r"^Table \d+\.?\s*", "", caption).lower()
    if "default" in caption:
        return "MAIN"
    caption = re.sub(r"\b(registers?|address(es)?|maps?|mapping|summary|description)\b", " ", caption)
    caption = re.sub(r"[^a-z0-9]+", "_", caption).strip("_").upper()
    return caption or "MAIN"


def _is_map_table(table: Table) -> bool:
    if table.columns < 3 or table.rows < 3:
        return False
    head = " ".join(_clean(table.cell(x, 0)).lower() for x in range(table.columns))
    return "name" in head and ("address" in head or "hex" in head or "addr" in head)


def _parse_map_table(table: Table) -> dict[str, SensorRegister]:
    rows = _rows(table)
    heads = [h.lower() for h in rows[0]]
    if table.rows > 1 and all(table.cell(x, 1)._head for x in range(table.columns)):
        heads = [f"{a} {b}".lower() for a, b in zip(rows[0], rows[1])]
    try:
        cname = next(i for i, h in enumerate(heads) if "name" in h)
        caddr = next(i for i, h in enumerate(heads) if "hex" in h or ("addr" in h and "bin" not in h))
    except StopIteration:
        return {}
    ctype = next((i for i, h in enumerate(heads) if "type" in h or "access" in h), None)
    cdefault = next((i for i, h in enumerate(heads) if "default" in h or "reset" in h), None)
    registers = {}
    for y, row in enumerate(rows):
        if table.cell(0, y)._head:
            continue
        name, address = row[cname], re.sub(r"h$|^0x", "", row[caddr])
        name = re.sub(r"\s*\((?:ack\d*|r/w|r|w)\)$", "", name, flags=re.IGNORECASE)  # FF_WU_SRC_1 (ack1)
        if name.upper().startswith("RESERVED"):
            continue
        # CALIB_0..F at 30-3F: one register per address
        if (m := re.fullmatch(r"(\w+?)([0-9A-F])\.\.([0-9A-F])", name)) and (
            a := re.fullmatch(r"([0-9A-Fa-f]{2})\s*-\s*([0-9A-Fa-f]{2})", address)
        ):
            first, last = int(a.group(1), 16), int(a.group(2), 16)
            if last - first == int(m.group(3), 16) - int(m.group(2), 16):
                access = row[ctype].replace(" ", "").upper() if ctype is not None else None
                for index in range(last - first + 1):
                    alias = f"{m.group(1)}{int(m.group(2), 16) + index:X}"
                    registers.setdefault(alias, SensorRegister(alias, first + index, access))
            continue
        if not re.fullmatch(r"[0-9A-Fa-f]{1,4}", address):
            continue
        reset = None
        if cdefault is not None and re.fullmatch(r"[01 ]{8,}", row[cdefault]):
            reset = int(row[cdefault].replace(" ", ""), 2)
        access = row[ctype].replace(" ", "").upper() if ctype is not None else None
        # STATUS_REG / STATUS_SPIAux: two names for the same register
        for alias in re.split(r"\s*/\s*", name):
            registers.setdefault(alias, SensorRegister(alias, int(address, 16), access, reset))
    return registers


def _layout_rows(table: Table) -> list[list[str]]:
    """Rows of a bit layout table with 8, 16, 24 or 32 cells, without bit number rows."""
    rows = []
    for row in _rows(table):
        row = [re.sub(r"\s*\(\d\)$", "", c) for c in row]  # footnote markers as text: 0(1)
        if all(re.fullmatch(r"((b|bit)?\s*\d+\s*)+", c, re.IGNORECASE) for c in row):
            continue  # bit numbers or a register value
        if len(row) == 9 and re.match(r"(bit|name|reg)", row[0], re.IGNORECASE):
            row = row[1:]
        if (
            len(row) in (8, 16, 24, 32)
            and sum(not _CONSTANT.match(c) for c in row)
            and (sum(bool(c) for c in row) > 2 or len(set(row)) <= 2)
        ):
            rows.append(row)
    return rows


def _description_rows(table: Table) -> list[tuple[str, str]]:
    """(field name, description) of a field description table, None if it is none."""
    if table.columns < 2:
        return None
    rows = _rows(table)
    pairs = []
    for y, row in enumerate(rows):
        if table.cell(0, y)._head or row[0].lower() in ("name", "bit", "field", "bit name"):
            continue
        name, text = row[0], row[-1]
        # [5:3] | AVGT2-0: To select the numbers of averaged temperature samples
        if re.fullmatch(r"\[\d+(:\d+)?\]", name) and (m := re.match(r"([A-Za-z][\w\-, ]*?)\s*:\s*(.*)", text)):
            name, text = m.group(1), m.group(2)
        pairs.append((name, text))
    if not pairs:
        return None
    # FS[1:0]_XL, FS1-FS0, HPM1, HPM0, ODR 3:0
    names = sum(bool(re.fullmatch(r"[A-Za-z][\w ,:\-\[\]]*", n)) and len(n) < 50 for n, _ in pairs)
    texts = sum(len(t) > 3 and not _BITS.match(t) for _, t in pairs)
    limit = 0.5 if "description" in table.caption().lower() else 0.7
    if names < limit * len(pairs) or texts < limit * len(pairs):
        return None
    return pairs


def _description_group(name: str, cells: list[str]):
    """
    The cells a field description name refers to -> (field name, cell indices).

    Handles `FS[1:0]_XL`, `ODR 3:0`, `ODR3-0`, `FS1-FS0`, `DO2 to DO0`, `Ref 7-Ref0`,
    `FS1, FS0` and `I2CFG2I2CFG1I2CFG0`.
    """
    compact = name.replace(" ", "")
    norm_cells = [_norm(c) for c in cells]
    if rn := _range_name(compact):
        prefix, msb, lsb, suffix = rn
    elif m := re.fullmatch(r"([A-Za-z_]*?[A-Za-z_])(\d+)(?:-|to|:)\1?(\d+)(\w*)", compact):
        prefix, msb, lsb, suffix = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    elif m := re.fullmatch(r"([A-Za-z_]+)(\d+):(\d+)", compact):
        prefix, msb, lsb, suffix = m.group(1), int(m.group(2)), int(m.group(3)), ""
    else:
        # FS1, FS0 or I2CFG2I2CFG1I2CFG0: cells of the same name with different numbers
        key = _norm(name)
        indices = [
            i for i, c in enumerate(norm_cells) if c and len(c) > 1 and c in key and not _CONSTANT.match(cells[i])
        ]
        skeletons = {re.sub(r"\d+", "", norm_cells[i]) for i in indices}
        contiguous = len(indices) > 1 and indices == list(range(indices[0], indices[-1] + 1))
        if len(skeletons) == 1 and contiguous and len(key) == sum(len(norm_cells[i]) for i in indices):
            return re.sub(r"_?\d+$", "", cells[indices[0]].replace(" ", "")), indices
        return None
    msb, lsb = max(msb, lsb), min(msb, lsb)
    field = (prefix + suffix).strip("_").replace("__", "_")
    pattern = re.compile(rf"{re.escape(_norm(prefix))}(\d+){re.escape(_norm(suffix))}")
    indices = []
    for index, cell in enumerate(norm_cells):
        if (m := pattern.fullmatch(cell)) and lsb <= int(m.group(1)) <= msb or cell == _norm(field):
            indices.append(index)
    if not indices:
        return None
    # A typo in one cell (RSPDS5 between RPDS6 and RPDS4) does not split the field
    window = list(range(indices[0], indices[-1] + 1))
    if len(window) - len(indices) == 1 and len(window) == msb - lsb + 1:
        gap = next(i for i in window if i not in indices)
        if not _CONSTANT.match(cells[gap]):
            indices = window
    return field, indices


def _merge_bits(cells: list[str], descriptions: list[tuple[str, str]]) -> list[SensorField]:
    """Bit cells (MSB first) to fields, using the field names of the description table."""
    width = len(cells)
    assigned = {}
    texts = {}
    for name, text in descriptions:
        if group := _description_group(name, cells):
            field, indices = group
            for index in indices:
                assigned.setdefault(index, field)
            texts.setdefault(_norm(field), text)
        texts.setdefault(_norm(name), text)
    for index, cell in enumerate(cells):
        if index not in assigned and not _CONSTANT.match(cell):
            assigned[index] = cell.replace(" ", "")
    bits = defaultdict(list)
    for index, name in assigned.items():
        bits[name].append(width - 1 - index)
    # Without a description name: ODR3, ODR2, ODR1, ODR0 are one field, INT1_FSM8 .. INT1_FSM1 are not
    singles = {n for n, b in bits.items() if len(b) == 1 and _norm(n) not in texts}
    for name in sorted(singles):
        if name not in bits or not (m := re.fullmatch(r"(.*?[A-Za-z_])(\d+)", name)):
            continue
        group = [s for s in singles if s in bits and re.fullmatch(rf"{re.escape(m.group(1))}\d+", s)]
        if len(group) < 2 or f"{m.group(1)}0" not in group:
            continue
        positions = sorted(bits[s][0] for s in group)
        if positions == list(range(positions[0], positions[0] + len(positions))):
            for s in group:
                del bits[s]
            bits[m.group(1).rstrip("_")] = positions
    fields = []
    for name, positions in bits.items():
        positions = sorted(positions)
        if positions != list(range(positions[0], positions[-1] + 1)):
            # The same name on separate bits: one field per bit
            fields += [SensorField(f"{name}{i}", p, 1) for i, p in enumerate(positions)]
        else:
            fields.append(SensorField(name, positions[0], len(positions), texts.get(_norm(name))))
    return sorted(fields, key=lambda f: -f.position)


def _inline_values(field: SensorField, text: str) -> list[SensorEnum]:
    """
    Values listed in the description: `(00: ±250 dps; 01: ±500 dps; ...)`,
    `(0: disabled, 1: enabled)`, `000: Y = Y; (default) 001: Y = -Y; ... Others: Y = Y`
    or with decimal values `(0: ...; 1: ...; 2: ...)`.
    """
    # 1100-1111: not allowed ends the list like Others:
    items = list(re.finditer(r"(?:^|(?<=[\s(;,]))([01x]+|\d+)(\s*(?:-|to)\s*[01x]+)?\s*(?::|->|=(?=\s))\s*", text))
    if len(items) < 2:
        return []
    raw = []
    for item, following in zip(items, items[1:] + [None]):
        if item.group(2):
            continue
        end = following.start() if following else len(text)
        description = text[item.end() : end]
        description = re.split(r"[;,.]?\s*\b(?:Others?|Otherwise|Other values)\s*:", description)[0]
        description = re.sub(r"\(default\)", "", description)
        description = description.strip().strip(";,.").strip()
        if description.count(")") > description.count("("):
            description = description[: description.rindex(")")].strip().strip(";,.").strip()
        raw.append((item.group(1), description))
    values = [v for v, _ in raw]
    binary = [i for i, v in enumerate(values) if re.fullmatch(r"[01x]+", v) and len(v) == field.width]
    if field.width > 1 and len(binary) >= 2 and len(binary) < len(values):
        # "01: decimation 1: max(...)": only the values of the field width start an item
        merged = []
        for i, (v, d) in enumerate(raw):
            if i in binary or not merged:
                merged.append([v, d])
            else:
                merged[-1][1] += f" {v}: {d}"
        if 0 in binary:
            raw = [tuple(m) for m in merged]
            values = [v for v, _ in raw]
    if all(re.fullmatch(r"[01x]+", v) and len(v) == field.width for v in values):
        pass
    elif all(v.isdigit() for v in values) and all(int(v) < (1 << field.width) for v in values):
        values = [format(int(v), f"0{field.width}b") for v in values]
    else:
        return []
    if len(set(values)) != len(values):
        return []
    return [SensorEnum(v, d, d) for v, (_, d) in zip(values, raw) if d]


def _key_column(label: str, fields: list[SensorField], cells: dict[str, tuple]):
    """Header of a value table key column -> (field, bit or None for all bits)."""
    label = label.replace(" ", "")
    if label in cells:  # ODR_XL3
        return cells[label]
    if rn := _range_name(label):  # FS[1:0]_XL
        target = _norm(rn[0] + rn[3])
    else:
        target = _norm(label)
    for f in fields:
        if _norm(f.name) == target:
            return f, None
    return None


def _key_bits(cell: str) -> str:
    cell = re.sub(r"\s*\(default\)", "", cell).replace(" ", "")
    return cell if _BITS.match(cell) else None


def _table_values(table: Table, fields: list[SensorField], cells: dict[str, tuple]) -> list[tuple]:
    """
    Value table -> [(field, [SensorEnum])].

    The leading columns are the bits of one or several fields (ODR_XL3 .. ODR_XL0,
    FS[1:0]_XL), the other columns describe the values. In a table of several
    fields (DR | BW | ODR [Hz] | Cut-off) a field gets the columns that only
    depend on its own value (DR -> ODR [Hz]).
    """
    rows = _rows(table)
    if len(rows) < 2 or table.columns < 2:
        return []
    first = next((y for y, r in enumerate(rows) if _key_bits(r[0])), None)
    if not first:
        return []
    header = rows[first - 1]
    keys = []
    for x, cell in enumerate(rows[first]):
        # 100 in the column ODR [Hz] is not a key
        if not _key_bits(cell) or not (key := _key_column(header[x], fields, cells)):
            break
        keys.append(key)
    nkeys = len(keys)
    if not nkeys or nkeys == table.columns:
        return []
    groups = {}
    for x, (f, bit) in enumerate(keys):
        groups.setdefault(id(f), (f, []))[1].append((x, bit))
    data = [r for r in rows[first:] if all(_key_bits(c) for c in r[:nkeys])]
    labels = [" ".join(dict.fromkeys(rows[y][x] for y in range(first) if rows[y][x])) for x in range(table.columns)]
    # Columns of other fields after the keys (HF_ODR) are conditions, not names
    conditions = {x for x in range(nkeys, table.columns) if _key_column(header[x], fields, cells)}
    texts = [x for x in range(nkeys, table.columns) if x not in conditions]

    def value_of(row, field, columns):
        if len(columns) == 1 and columns[0][1] is None:
            value = _key_bits(row[columns[0][0]])
        else:
            value = "".join(_key_bits(row[x]) for x, _ in sorted(columns, key=lambda c: -c[1]))
        return value if len(value) == field.width else None

    def name_of(row, x):
        name = row[x]
        unit = re.search(r"\[(\w+)\]|\(([kmµμ]?(?:Hz|g|dps|s))\)", labels[x])
        if re.fullmatch(r"[\d.]+", name) and unit:
            name = f"{name} {unit.group(1) or unit.group(2)}"  # 12.5 in the column "ODR selection [Hz]"
        return name

    result = []
    for field, columns in groups.values():
        bits = [b for _, b in columns]
        if (None in bits and len(bits) > 1) or (None not in bits and sorted(bits) != list(range(field.width))):
            continue
        by_value = defaultdict(list)
        for row in data:
            if value := value_of(row, field, columns):
                by_value[value].append(row)
        if len(groups) == 1:
            useful = texts
        else:
            useful = [x for x in texts if all(len({r[x] for r in rs}) == 1 for rs in by_value.values())]
            useful = [x for x in useful if any(r[x] not in ("", "-") for rs in by_value.values() for r in rs)]
        if not useful:
            continue
        values = []
        for value, value_rows in by_value.items():
            for row in value_rows if len(groups) == 1 else value_rows[:1]:
                column = next((x for x in useful if row[x] not in ("", "-")), None)
                if column is None:
                    continue
                parts = [(x, row[x]) for x in useful + sorted(conditions) if row[x] not in ("", "-")]
                if len(parts) > 1:
                    description = "; ".join(f"{labels[x]}: {text}" for x, text in parts)
                else:
                    description = row[column]
                values.append(SensorEnum(value, name_of(row, column), description))
        if len({v.value for v in values}) >= 2:
            result.append((field, values))
    return result


def _add_values(field: SensorField, values: list[SensorEnum]):
    """Values from several tables or from the description and a table, one entry per value."""
    existing = {v.value: v for v in field.values}
    for value in values:
        if previous := existing.get(value.value):
            if value.description and value.description not in (previous.description or ""):
                previous.description = f"{previous.description} | {value.description}"
        else:
            field.values.append(value)
            existing[value.value] = value


class _Section:
    def __init__(self, heading: str, chapter: str):
        self.heading = heading
        self.chapter = chapter
        self.registers = [(n, int(a, 16)) for n, a in _REG_HEADING.findall(heading)]
        if not self.registers:
            # 7.6 CTRL_REG1 or OUT_X_L_A, OUT_X_H_A: the address is in the map table
            names = re.sub(r"^[\d.]+\s*", "", heading)
            if re.fullmatch(rf"{_NAME}(\s*(,|and)\s*{_NAME})*", names) and not re.search(
                r"regist", names, re.IGNORECASE
            ):
                self.registers = [(n, None) for n in re.split(r"\s*(?:,|and)\s*", names)]
        self.tables = []
        self.texts = []


class DatasheetSensor(Document):
    def __init__(self, path: str):
        super().__init__(path)

    def __repr__(self) -> str:
        return f"DSsensor({self.fullname})"

    @cached_property
    def part(self) -> str:
        """The part name in the title, for example `LSM6DSO`."""
        chapter = next(iter(sorted(self.chapters("chapter 0"), key=lambda c: c.number)), None)
        heading = next(iter(chapter.headings()), None) if chapter else None
        return re.sub(r"[^\w-]", "", _clean(heading)) if heading else self.name

    def _sections(self):
        for chapter in sorted(self.chapters(r"regist|features pages"), key=lambda c: c.number):
            section = None
            for item in chapter.items:
                if isinstance(item, Heading):
                    if section:
                        yield section
                    section = _Section(_clean(item), chapter.name)
                elif section is not None and isinstance(item, Table):
                    section.tables.append(item)
                elif section is not None and isinstance(item, Text) and not section.tables:
                    section.texts.append(_clean(item))
            if section:
                yield section

    @cached_property
    def register_map(self) -> dict[str, dict[str, SensorRegister]]:
        """Registers by bank and name. Banks come from the register address map tables."""
        banks = defaultdict(dict)
        described = []
        bank = None
        for section in self._sections():
            for table in section.tables:
                if _is_map_table(table):
                    bank = self._add_map(banks, table, section.chapter) or bank
            if section.registers:
                described += [(bank, r) for r in self._parse_section(section)]
        index = defaultdict(list)
        for registers in banks.values():
            for name, register in registers.items():
                index[_norm(name)].append(register)
        for bank, register in described:
            names = re.split(r"\s*/\s*", register.name)  # REFERENCE/DATACAPTURE
            candidates = [c for n in names for c in index.get(_norm(n), [])]
            if register.address is not None:
                candidates = [c for c in candidates if c.address == register.address] or candidates
            if not candidates and bank and register.address is not None:
                # The description renames the register: SLAVE0_CONFIG is SLV0_CONFIG, DRD_SRC is D6D_SRC
                words = set(re.findall(r"[a-z]{3,}", register.name.lower()))
                candidates = [
                    c
                    for c in banks[bank].values()
                    if c.address == register.address and words & set(re.findall(r"[a-z]{3,}", c.name.lower()))
                ]
            if candidates:
                target = candidates[0]
                target.description = target.description or register.description
                if register.fields and not target.fields:
                    target.cells, target.fields, target.size = register.cells, register.fields, register.size
            elif register.address is not None:
                # Registers without a map table
                banks["MAIN"].setdefault(names[0], register)
                register.name = names[0]
        return {bank: dict(sorted(r.items(), key=lambda kv: kv[1].address)) for bank, r in banks.items() if r}

    @staticmethod
    def _add_map(banks, table, chapter: str) -> str:
        registers = _parse_map_table(table)
        if not registers:
            return None
        # Several maps of the same registers (MPL3115 FIFO modes) are one bank
        for bank, existing in banks.items():
            same = sum(n in existing and existing[n].address == r.address for n, r in registers.items())
            if same >= 0.8 * len(registers):
                for name, register in registers.items():
                    existing.setdefault(name, register)
                return bank
        bank = _bank_name(table.caption())
        if bank in banks:
            # "Register address map" in the chapter "Sensor hub register mapping"
            bank = _bank_name(re.sub(r"^chapter \d+ ", "", chapter))
        while bank in banks:
            bank += "_"
        banks[bank] = registers
        return bank

    def _parse_section(self, section: _Section) -> list[SensorRegister]:
        registers = [SensorRegister(n, a) for n, a in section.registers]
        description = next((t for t in section.texts if t and not t.startswith("Table")), None)
        # OFFSET_X_REG_L (45h) and OFFSET_X_REG_H (46h): the text describes all of them
        for register in registers:
            register.description = description
        descriptions = []
        layouts = []
        others = []
        for table in section.tables:
            if _is_map_table(table):
                continue
            if rows := _layout_rows(table):
                layouts.append((table, rows))
                # Some layout tables continue with the field descriptions: D[31:0] | Timestamp output
                for row in _rows(table):
                    cells = [c for c in row if c]
                    if len(cells) == 2 and len(row) > 2 and re.match(r"[A-Za-z]", cells[0]) and len(cells[1]) > 3:
                        descriptions.append((cells[0], cells[1]))
            elif pairs := _description_rows(table):
                descriptions += pairs
            else:
                others.append(table)
        # Layout rows to registers: by register name in the caption, otherwise in order
        pending = list(registers)
        for table, rows in layouts:
            caption = _clean(table.caption()).upper()
            named = [r for r in pending if re.search(rf"\b{re.escape(r.name.upper())}\b", caption)]
            targets = named or pending
            if len(targets) > 1 and len(rows) > 1 and all(r.address is not None for r in targets):
                # D31..D24, D23..D16, ...: the most significant byte is at the highest address
                highest = [max((int(n) for c in row for n in re.findall(r"(\d+)$", c)), default=-1) for row in rows]
                targets = sorted(targets, key=lambda r: r.address)
                if highest == sorted(highest, reverse=True) and highest[0] > highest[-1]:
                    targets = targets[: len(rows)][::-1]
            for register, cells in list(zip(targets, rows)):
                register.cells, register.size = cells, len(cells)
                pending.remove(register)
        for register in registers:
            if not register.cells:
                continue
            register.fields = _merge_bits(register.cells, descriptions)
            cells = {}
            for f in register.fields:
                for bit in range(f.width):
                    cell = register.cells[register.size - 1 - (f.position + bit)].replace(" ", "")
                    cells.setdefault(cell, (f, bit))
                if f.description:
                    f.values = _inline_values(f, f.description)
            parsed = set()
            for table in others:
                for field, values in _table_values(table, register.fields, cells):
                    _add_values(field, values)
                    parsed.add(id(field))
            # Value tables in other chapters: "Refer to Table 26"
            for f in register.fields:
                if id(f) in parsed or not f.description:
                    continue
                for number in re.findall(r"Table\s*(\d+)", f.description):
                    table = self._tables.get(int(number))
                    if table is not None and table not in others:
                        for field, values in _table_values(table, register.fields, cells):
                            if field is f:
                                _add_values(field, values)
        return registers

    @cached_property
    def _tables(self) -> dict[int, Table]:
        tables = {}
        for chapter in self.chapters():
            for table in chapter.tables():
                if m := re.match(r"Table\s*(\d+)", table.caption()):
                    tables.setdefault(int(m.group(1)), table)
        return tables
