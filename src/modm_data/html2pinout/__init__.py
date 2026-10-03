# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# HTML to Board Pinout Pipeline

Extracts the connector pinouts (ST morpho, ARDUINO, Zio) of the NUCLEO boards
from the connector tables of their user manuals.
"""

import re
import logging
from collections import defaultdict
from ..html.document import Document
from .transcribed import BRIDGES, TRANSCRIBED

__all__ = ["board_pinouts_from_user_manual", "board_bridges_from_user_manual", "inherit_bridge_defaults"]

LOGGER = logging.getLogger(__name__)

_TABLES = r"morpho|arduino|zio"
_NUMBER = re.compile(r"^(zio|connector)?pin(number|no\.?|nbr)?$")
_CONNECTOR = re.compile(r"^(connector|cn)$")
_MCU = re.compile(r"(stm32|mcu|stlink).*(pin?|port|name)$|^port$")
_GPIO = re.compile(r"(?<![A-Z0-9])P[A-Q]\d{1,2}(?!\d)")
_BRIDGE = re.compile(r"\b(?:SB|JP)\d+\b")
_STLINK = re.compile(r"(?:P[A-K]\d+\W+(?:and\W+)?)+(?:on|of)\W+(?:the\W+)?ST-?LINK")
_CN = re.compile(r"\bCN\d+\b")
_BOARD = re.compile(r"NUCLEO-(?:[FGLHUCN]|WBA?|WL)\d(?![0-9A-Z]*XX)[0-9A-Z]*(?:-[PQ])?\b")

# The manuals spell the same signal in many ways
_SIGNALS = [
    (r"|-|NA|NC.*|OFF.*|RES|ARD RES|RESERVED.*|NOT CONNECTED.*|ARDUINO (SUPPORT|COMPATIBLE).*", "NC"),
    (r"GROUND", "GND"),
    (r"RESET", "NRST"),
    (r"\+?3(\.3 ?V|V3)", "3V3"),
    (r"\+?5 ?V( OUTPUT)?", "5V"),
    (r"5V_EXT", "E5V"),
    (r"U5V|5V[-_](USB_)?STL(IN)?K|VBUS_STL ?K", "5V_STLK"),
    (r"5V[-_]USB[-_]CHGR?", "5V_USB_CHGR"),
    (r"5V[_ ]INT.*", "5V_INT"),
    (r"IO ?REF|3V3 \(IOREF\)|3V3 I/O", "IOREF"),
    (r"VDDA|AVVD/VREF\+", "AVDD"),
    (r"VREF\+", "VREFP"),
    (r"VREF-", "VREFM"),
    (r"(OSC_(IN|OUT)).*", r"\1"),
]
_KINDS = {
    "nc": r"NC",
    "ground": r"A?GND",
    "system": r"NRST|BOOT\d|OSC_(IN|OUT)",
    "power": r"\d(\.\d)?V\S*|[AE]?VDD\S*|VIN|VBAT|VLCD|VREF\S*|V_TARGET|E5V|IOREF|AREF",
}
_FIELDS = {
    "net": r"signalname",
    "function": r"function|feature",
    "remark": r"remark|comment",
    "bridge": r"solderbridge",
}


def _text(cell) -> str:
    if cell is None:
        return ""
    text = re.sub(r"<[^>]+>", "", cell.text(sup="", br=" "))
    return re.sub(r"\s+", " ", re.sub(r"\(\d+\)", "", text)).strip()


def _key(text: str) -> str:
    return text.replace(" ", "").lower()


def _signal(text: str) -> tuple[str, str]:
    """:return: the normalized signal name and its kind: gpio, power, ground, system, nc or other."""
    if gpio := _GPIO.search(text):
        return gpio.group(0), "gpio"
    # Footnote leftovers and line breaks inside of names
    text = re.sub(r" ?_ ?", "_", re.sub(r" ?\d\)$", "", text.upper()))
    for pattern, name in _SIGNALS:
        if match := re.fullmatch(pattern, text):
            text = match.expand(name)
            break
    return text, next((kind for kind, pattern in _KINDS.items() if re.fullmatch(pattern, text)), "other")


def _field(header: str) -> str:
    """:return: the normalized name of a table column."""
    if _MCU.search(_key(header)):
        return "pin"
    return next((name for name, pattern in _FIELDS.items() if re.search(pattern, _key(header))), "label")


def _boards(html: str) -> set[str]:
    return set(_BOARD.findall(html))


def _document_boards(chapters: list) -> set[str]:
    # The title, features and ordering information name the boards of the manual, later chapters also name others
    boards = set().union(*(_boards(c._path.read_text()) for c in chapters if c.number <= 2))
    return boards or set().union(*(_boards(c._path.read_text()) for c in chapters))


def _groups(header: list[str]) -> dict[int, list[int]]:
    """
    Tables place several connector pins side by side in one row. Every pin
    number column owns the columns describing it, which are either repeated
    (`Pin | Name | Pin | Name`) or mirrored (`Name | Pin | Pin | Name`).
    """
    numbers = [x for x, h in enumerate(header) if _NUMBER.match(_key(h))]
    if not numbers:
        return {}
    groups = {n: [] for n in numbers}
    groups[numbers[0]] += range(0, numbers[0])
    groups[numbers[-1]] += range(numbers[-1] + 1, len(header))
    for a, b in zip(numbers, numbers[1:]):
        between = list(range(a + 1, b))
        if between and b + 1 < len(header) and header[a + 1] == header[b + 1]:
            groups[a] += between
        elif between and a > 0 and header[a - 1] == header[b - 1]:
            groups[b] += between
        else:
            half = len(between) // 2
            groups[a] += between[:half]
            groups[b] += between[len(between) - half :]
    return groups


def _table_pinout(table, kind: str) -> dict[str, dict]:
    if not table._hrows:
        return {}
    hy = table._hrows - 1
    header = [_text(table.cell(x, hy)) for x in range(table.columns)]
    rows = range(table._hrows, table.rows)
    if not (groups := _groups(header)):
        # Some tables only name the connectors in the header, then the pin numbers are found by their content
        digits = [sum(_text(table.cell(x, y)).isdigit() for y in rows) for x in range(table.columns)]
        hy, header = table._hrows, ["Pin" if d > len(rows) * 0.8 else "Pin name" for d in digits]
        groups = _groups(header)
    captions = set(_CN.findall(table.caption()))
    pinout = defaultdict(dict)
    unnamed = 1

    for number, columns in groups.items():
        # The connector is named in the header above, in its own column or in the caption
        above = " ".join(_text(table.cell(number, y)) for y in range(hy))
        connector = next(iter(_CN.findall(above)), next(iter(captions), "") if len(captions) == 1 else "")
        connector_columns = [
            c
            for c in columns
            if _CONNECTOR.match(_key(header[c])) or any(_CN.match(_text(table.cell(c, y))) for y in rows)
        ]
        # The dedicated STM32 pin column is more trustworthy than free-form function columns
        columns = sorted(set(columns) - set(connector_columns), key=lambda c: (not _MCU.search(_key(header[c])), c))

        for y in rows:
            if not (pin := _text(table.cell(number, y))).isdigit():
                continue
            for c in connector_columns:
                if text := _text(table.cell(c, y)):
                    connector = next(iter(_CN.findall(text)), text)
            fields = defaultdict(list)
            for c in sorted(columns):
                if text := _text(table.cell(c, y)):
                    fields[_field(header[c])].append(text)
            signals = [_signal(_text(table.cell(c, y))) for c in columns]
            mcu = [s for c, s in zip(columns, signals) if _MCU.search(_key(header[c]))]
            # ponytail: only the first GPIO is the signal, solder bridge alternatives stay in the pin field
            # Without a GPIO in the STM32 pin column, only a supply or system signal can be on the pin,
            # everything else is just the name of the unconnected connector pin.
            signal = next((s for s in mcu or signals if s[1] == "gpio"), None)
            signal = signal or next((s for s in signals if s[1] in {"power", "ground", "system"}), None)
            signal = signal or next((s for s in signals if s[1] == "other" and not mcu), ("NC", "nc"))
            # Unnamed connectors are numbered in table order, a repeated pin number starts the next one
            if not connector and int(pin) in pinout[f"{kind}{unnamed}"]:
                unnamed += 1
            pinout[connector or f"{kind}{unnamed}"][int(pin)] = {
                "signal": signal[0],
                "kind": signal[1],
                **{name: " / ".join(texts) for name, texts in fields.items()},
            }

    # Tables without any GPIO describe something else than a pinout
    if not any(p["kind"] == "gpio" for pins in pinout.values() for p in pins.values()):
        return {}
    return {c: pins for c, pins in pinout.items() if pins}


def board_pinouts_from_user_manual(document: Document) -> dict[str, dict[str, dict]]:
    """
    :return: `board -> connector -> {type, side, pins: number -> {signal, kind, ...}}` for all NUCLEO boards in
             the user manual. The signal names are normalized, the original table cells are kept as `pin` (STM32
             pin), `label` (connector pin name), `net`, `function`, `remark` and `bridge`.
    """
    chapters = sorted(document.chapters(), key=lambda c: c.number)
    boards = _document_boards(chapters)
    pinouts = defaultdict(dict)

    for chapter in chapters:
        for table in chapter.tables():
            # Not all tables have a caption, but then their heading describes them
            kind = re.search(_TABLES, table.caption(), re.IGNORECASE) or re.search(
                _TABLES, table.heading(), re.IGNORECASE
            )
            if not kind or not (pinout := _table_pinout(table, kind.group(0).lower())):
                continue
            # Some manuals describe several boards with one table for each
            for board in _boards(table.caption()) or boards:
                for connector, pins in pinout.items():
                    # A mostly different pinout is another connector with a wrong caption
                    known = pinouts[board].get(connector, {"pins": {}})["pins"]
                    if sum(n in known and known[n]["signal"] != p["signal"] for n, p in pins.items()) > len(pins) / 2:
                        LOGGER.warning(f"{document.fullname}: {board} {connector} is redefined in '{table.caption()}'")
                        connector += "-2"
                    current = pinouts[board].setdefault(connector, {"type": kind.group(0).lower(), "pins": {}})["pins"]
                    for number, pin in pins.items():
                        if number in current and current[number]["signal"] != pin["signal"]:
                            LOGGER.warning(
                                f"{document.fullname}: {board} {connector}-{number} conflict: "
                                f"{current[number]['signal']} vs {pin['signal']} in '{table.caption()}'"
                            )
                            continue
                        current[number] = pin

    # Some pinouts are only in a figure or got lost in the conversion to HTML
    source, transcribed = TRANSCRIBED.get(document.name, ("", {}))
    for connectors in pinouts.values():
        for connector, names in transcribed.items():
            pins = connectors.setdefault(connector, {"type": "morpho", "pins": {}})["pins"]
            for number, name in enumerate(names.split(), start=1):
                signal, kind = _signal(name)
                pins.setdefault(number, {"signal": signal, "kind": kind, "label": name, "remark": f"From {source}"})

    for connectors in pinouts.values():
        for connector in connectors.values():
            connector["pins"] = dict(sorted(connector["pins"].items()))
        # The manuals list the left morpho connector first
        morphos = [c for c in connectors.values() if c["type"] == "morpho"]
        if len(morphos) == 2:
            morphos[0]["side"], morphos[1]["side"] = "left", "right"
    return dict(sorted(pinouts.items()))


def board_bridges_from_user_manual(document: Document) -> dict[str, list[dict]]:
    """
    Solder bridges and jumpers change what a pin is connected to. The manuals describe their effect only as
    text, so the bridges are linked to the GPIOs that their descriptions mention.

    :return: `board -> [{ids, name, source, gpios, pins, states: [{state, default, text}]}]` for all NUCLEO boards in
             the user manual. The default state is printed in bold. `pins` maps a single bridge of a group to
             its GPIOs, where the manual says so.
    """
    chapters = sorted(document.chapters(), key=lambda c: c.number)
    boards = _document_boards(chapters)
    bridges = defaultdict(dict)

    for chapter in chapters:
        for table in chapter.tables(r"solder bridge|SB configuration"):
            if not table._hrows:
                continue
            columns, rows = range(table.columns), range(table._hrows, table.rows)
            header = [_key(_text(table.cell(x, table._hrows - 1))) for x in columns]
            # The headers are ambiguous, so the bridge column is the first one that names bridges in most rows.
            # Columns to its right may list even more of them, for example the mutually exclusive ones.
            named = [sum(bool(_BRIDGE.search(_text(table.cell(x, y)))) for y in rows) for x in columns]
            state = next((x for x in columns if re.search(r"state|setting|status|value|position", header[x])), None)
            if not max(named) or state is None:
                continue
            bridge = next(x for x in columns if named[x] >= max(named) / 2)
            others = [x for x in columns if x not in {bridge, state}]

            for y in rows:
                cell = _text(table.cell(bridge, y))
                if not (ids := tuple(dict.fromkeys(_BRIDGE.findall(cell)))):
                    continue
                texts = list(dict.fromkeys(t for x in others if (t := _text(table.cell(x, y)))))
                # Brackets behind the bridges either name them all or list the GPIOs of each one. Without
                # a name there, it is in the definition column left of the bridges.
                notes = re.findall(r"\(([^()]*)\)", cell)
                name = " / ".join(dict.fromkeys(n.strip() for n in notes if _GPIO.sub("", n).strip(" /,")))
                name = name or next((_text(table.cell(x, y)) for x in others if x < bridge), "")
                pins = {i: _GPIO.findall(n) for i, n in re.findall(r"((?:SB|JP)\d+)\s*\(([^()]*)\)", cell)}
                text = " ".join(t for t in texts if t != name)
                # The pins of the ST-LINK microcontroller are not the pins of the board
                gpios = _GPIO.findall(_STLINK.sub("", f"{cell} {name} {text}"))
                for board in _boards(table.caption()) or boards:
                    # Boards made of two PCBs number their bridges twice, so the table is part of the identity
                    entry = bridges[board].setdefault(
                        (table.caption(), ids),
                        {"ids": list(ids), "name": name, "source": _text(table._caption), "gpios": [], "states": []},
                    )
                    entry.setdefault("pins", {}).update({i: g for i, g in pins.items() if g})
                    entry["gpios"] = sorted(set(entry["gpios"]) | set(gpios))
                    option = {
                        "state": _text(table.cell(state, y)),
                        "default": "<b>" in table.cell(state, y).html,
                        "text": text,
                    }
                    # Some tables list a bridge once for every feature it affects
                    same = next((o for o in entry["states"] if o["state"] == option["state"]), None)
                    if same is None:
                        entry["states"].append(option)
                    else:
                        same["default"] |= option["default"]
                        if text not in same["text"]:
                            same["text"] += " " + text

    # A bridge with only one described state is delivered in it
    for entries in bridges.values():
        for entry in entries.values():
            if len(entry["states"]) == 1:
                entry["states"][0]["default"] = True

    return {
        board: list(bridges[board].values()) + BRIDGES.get(board, [])
        for board in sorted(boards | bridges.keys())
        if board in bridges or board in BRIDGES
    }


def inherit_bridge_defaults(bridges: dict[str, list[dict]], older: dict[str, list[dict]], source: str):
    """
    Newer revisions of a manual sometimes lose the bold formatting of the default state. The older revision
    still has it, so bridges without a default take the one of the same bridge there.
    """
    for board, entries in bridges.items():
        known = {tuple(b["ids"]): b for b in older.get(board, [])}
        for bridge in entries:
            if any(o["default"] for o in bridge["states"]) or not (old := known.get(tuple(bridge["ids"]))):
                continue
            defaults = {o["state"] for o in old["states"] if o["default"]}
            if len(defaults) == 1 and defaults & {o["state"] for o in bridge["states"]}:
                bridge["default_from"] = source
                for option in bridge["states"]:
                    option["default"] = option["state"] in defaults
