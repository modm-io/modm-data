# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

"""
# CMSIS Board Pack to Board Pinout Pipeline

Extracts the same board pinouts as `modm_data.html2pinout` from the netlists of
the STM32CubeMX2 board packs (`*_hw-board` with schema 1.0.0 descriptors). The
connector pins are followed through the netlist to the device pins, through
resistors and the solder bridges and jumpers in their default state.
"""

import json
import re
from functools import cache
from pathlib import Path
from xml.etree import ElementTree

from ..html2pinout import _signal
from ..html2pinout.signals import _USES

__all__ = ["board_pinout"]

# Arduino UNO R3 header pin names by the connector role in connectors.json
_ARDUINO = {
    "POWER": {i + 1: n for i, n in enumerate(["NC", "IOREF", "NRST", "3V3", "5V", "GND", "GND", "VIN"])},
    "ANALOG": {i + 1: f"A{i}" for i in range(6)},
    "DIGITAL TOP": {**{i + 1: f"D{i + 8}" for i in range(6)}, 7: "GND", 8: "AREF", 9: "D14", 10: "D15"},
    "DIGITAL BOTTOM": {i + 1: f"D{i}" for i in range(8)},
}
# ponytail: morpho connectors by their part number (Samtec TSW 2x16 and up), the packs do not list them as connectors
_MORPHO = re.compile(r"TSW-1(\d\d)-\d\d-\w-D")
_HEADER = re.compile(r"SSW-1(\d\d)-")
# Parts that only pass a signal on, everything else is a use of the pin on the board
_PASSIVE = re.compile(r"(R|C|SB|JP|TP|CN|L|FB|D|T|Q)\d+")


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


@cache
def _dfp_pinout(dfps: Path, device: str) -> tuple[dict[str, str], dict[str, dict]]:
    """:return: package pin -> pad name, and gpio -> signal -> alternate function number or `None`."""
    for pdsc in dfps.glob("*/*.pdsc"):
        root = ElementTree.parse(pdsc).getroot()
        for dev in root.iter("device"):
            if (variant := dev.find(f".//variant[@Dvariant='{device}']")) is None:
                continue
            # The pinout is described by the variant (G5, H7P) or its device (C5)
            path = next(
                d.get("path") for e in (variant, dev) for d in e.iter("descriptor") if d.get("schemaType") == "pinout"
            )
            pinout = _json(pdsc.parent / path)

            def pad(name):
                return re.split(r"[(\-]", name)[0]

            signals = {}
            for s in pinout["signals"]:
                if s["name"] != "GPIO" and (af := s["function"]).get("type") != "user_gpio":
                    signals.setdefault(pad(s["die_pad"]), {})[s["name"]] = int(af["id"][2:]) if "id" in af else None
            pins = {b["position"]: pad(b["die_pad"]) for b in pinout["bonds"]}
            return pins, {g: dict(sorted(s.items())) for g, s in sorted(signals.items())}
    raise ValueError(f"No DFP describes {device}!")


class _Board:
    def __init__(self, pack: Path):
        pdsc = next(pack.glob("*.pdsc"))
        board = ElementTree.parse(pdsc).getroot().find(".//board")
        self.name = board.get("name")
        self.device = board.find("mountedDevice").get("Dname")
        desc = {d.get("schemaType"): pack / d.get("path") for d in board.iter("descriptor")}
        self.version = (pack / ".version").read_text() if (pack / ".version").exists() else ""
        netlist, config = _json(desc["netlist"]), _json(desc["configuration-pcb"])
        self.bom = {p["reference"]: p["part_number"] for p in _json(desc["bom"])["parts"]}
        self.mounted = {p["reference"] for p in _json(desc["partlist"])["parts"] if p["mounted"]}
        self.connectors = _json(desc["connectors"])["connectors"] if "connectors" in desc else []
        self.mcu = next(r for r, pn in self.bom.items() if pn == self.device)
        self.power = set(netlist["power_supplies"])
        self.nets = {n["netId"]: n for n in netlist["nets"]}
        self.pin2net = {(p["reference"], p["pin"]): n["netId"] for n in netlist["nets"] for p in n["net"]}
        self.pins = {}
        for ref, pin in self.pin2net:
            self.pins.setdefault(ref, []).append(pin)
        # The states of the solder bridges and jumpers: ref -> state -> pairs of shorted pins
        self.states, self.default = {}, config["defaultBoardConfig"]
        for name, element in config["elements"].items():
            for ref in element.get("subElements", [name]):
                self.states[ref] = {s: v["pinAssociations"] for s, v in element["possibleStates"].items()}

    def label(self, ref: str, pin: str) -> str:
        nid = self.pin2net.get((ref, pin))
        return self.nets[nid]["netLabel"] if nid is not None else ""

    def _through(self, ref: str, pin: str, any_state: bool) -> list[tuple[str, bool]]:
        """:return: the other pins of a part reachable from a pin, and whether only a non-default state connects them."""
        if ref in self.states:
            # A bridge that is not mounted can still be soldered in
            default = self.states[ref].get(self.default.get(ref), []) if ref in self.mounted else []
            pairs = [(p, False) for p in default] + [
                (p, True) for s in self.states[ref].values() for p in s if any_state
            ]
            return list(dict.fromkeys((q, alt) for pair, alt in pairs if pin in pair for q in pair if q != pin))
        if ref not in self.mounted:
            return []
        if re.fullmatch(r"R\d+", ref):
            return [(q, False) for q in self.pins[ref] if q != pin]
        # ponytail: LED drivers in SOT-23 with the base or gate on pin 1 and the collector or drain on pin 3
        if re.fullmatch(r"[TQ]\d+", ref) and pin == "1":
            return [("3", False)]
        return []

    def supply(self, ref: str, pin: str) -> str:
        """:return: the first named net behind closed bridges, for the unnamed nets of connector pins."""
        seen, todo = set(), [(ref, pin)]
        while todo:
            r, p = todo.pop(0)
            if (nid := self.pin2net.get((r, p))) is None or nid in seen:
                continue
            seen.add(nid)
            if not re.match(r"Net|CN\d+_\d+", label := self.nets[nid]["netLabel"]):
                return label
            for x in self.nets[nid]["net"]:
                if x["reference"] in self.states and x["reference"] in self.mounted:
                    todo += [(x["reference"], q) for q, alt in self._through(x["reference"], x["pin"], False)]
        return ""

    def reach(self, ref: str, pin: str, any_state: bool = False) -> list[tuple[str, str, list[str], bool]]:
        """:return: `(ref, pin, bridges, alternative)` of all part pins connected to a pin, power and ground excluded."""
        seen, out, todo = set(), [], [(self.pin2net.get((ref, pin)), [], False)]
        while todo:
            # The default connections first, so that a net is only an alternative if nothing else connects it
            todo.sort(key=lambda t: t[2])
            nid, path, alt = todo.pop(0)
            if nid is None or nid in seen:
                continue
            seen.add(nid)
            if (label := self.nets[nid]["netLabel"]) in self.power or re.match(r"A?GND|VSS", label):
                continue
            pins = [
                (p["reference"], p["pin"]) for p in self.nets[nid]["net"] if (p["reference"], p["pin"]) != (ref, pin)
            ]
            out += [(r, p, path, alt) for r, p in pins]
            # The search ends at the device pins, behind them are only their own uses
            if (path or ref != self.mcu) and any(r == self.mcu for r, _ in pins):
                continue
            for p in self.nets[nid]["net"]:
                if (p["reference"], p["pin"]) == (ref, pin):
                    continue
                bridge = path + [p["reference"]] if p["reference"] in self.states else path
                for q, a in self._through(p["reference"], p["pin"], any_state):
                    todo.append((self.pin2net.get((p["reference"], q)), bridge, alt or a))
        return out


def _connector_types(board: _Board) -> dict[str, tuple[str, dict[int, str]]]:
    """:return: connector -> (type, pin number -> name) of the ARDUINO and morpho connectors."""
    types = {}
    for connector in board.connectors:
        for role in connector["roles"]:
            if connector["name"] == "Arduino" and role["type"] in _ARDUINO:
                types[role["id"]] = ("arduino", _ARDUINO[role["type"]])
    for ref, part in sorted(board.bom.items(), key=lambda r: int(re.sub(r"\D", "", r[0]) or 0)):
        if ref.startswith("CN") and ref not in types and (m := _MORPHO.match(part)) and int(m.group(1)) >= 16:
            types[ref] = ("morpho", {n: "" for n in range(1, 2 * int(m.group(1)) + 1)})
    return types


def board_pinout(pack: Path, dfps: Path) -> dict:
    """
    :param pack: The folder of an extracted `*_hw-board` pack.
    :param dfps: The folder with the extracted HAL2 DFPs.
    :return: The pinout of the board in the format of `modm_data.html2pinout`:
             `{board, document, device, connectors, signals, bridges, uses}`.
    """
    board = _Board(pack)
    pads, signals = _dfp_pinout(dfps, board.device)

    def is_gpio(ref, pin):
        return ref == board.mcu and pads.get(pin, "").startswith("P")

    connectors = {}
    for name, (kind, numbers) in _connector_types(board).items():
        pins = {}
        for number, label in numbers.items():
            net = board.label(name, str(number))
            reached = board.reach(name, str(number), any_state=True)
            gpios = list(
                dict.fromkeys(pads[p] for r, p, _, alt in sorted(reached, key=lambda x: x[3]) if is_gpio(r, p))
            )
            default = [pads[p] for r, p, _, alt in reached if is_gpio(r, p) and not alt]
            bridges = list(dict.fromkeys(b for r, p, path, _ in reached if is_gpio(r, p) for b in path))
            if default:
                signal, kind_ = default[0], "gpio"
            elif not net:
                signal, kind_ = "NC", "nc"
            else:
                # The other device pins (NRST, BOOT0, VBAT) or the supply on the pin
                mcu = next((pads[p] for r, p, _, alt in reached if r == board.mcu and not alt), None)
                signal, kind_ = _signal(mcu or board.supply(name, str(number)) or "NC")
                if mcu is None and kind_ == "gpio":
                    signal, kind_ = net, "other"
            pins[number] = {
                "signal": signal,
                "kind": kind_,
                "label": label or ("" if re.match(r"Net|CN\d+_\d+", net) else net),
                "net": net,
                **({"pin": " / ".join(gpios)} if gpios else {}),
                **({"bridge": " / ".join(bridges)} if bridges else {}),
            }
        connectors[name] = {"type": kind, "pins": pins}
    morphos = [c for c in connectors.values() if c["type"] == "morpho"]
    if len(morphos) == 2:
        morphos[0]["side"], morphos[1]["side"] = "left", "right"

    # What the board itself uses the GPIOs for: the parts behind them that are not just passing the signal on
    uses = {}
    for pin, gpio in pads.items():
        if not gpio.startswith("P") or not board.label(board.mcu, pin):
            continue
        reached = board.reach(board.mcu, pin)
        parts = sorted({r for r, _, _, _ in reached if not _PASSIVE.fullmatch(r) and r != board.mcu})
        if not parts:
            continue
        # The nets next to the device are often only named after the GPIO, the ones further away after their use
        labels = [board.label(board.mcu, pin)] + [board.label(r, p) for r, p, _, _ in reached]
        label = next((x for x in labels if not re.match(r"Net|P[A-Z]\d+$|CN\d+_\d+", x)), labels[0])
        name = label.upper()
        use = next((use for use, pattern in _USES.items() if re.search(pattern, name)), None)
        use = use or next(
            (u for p, u in (("LD", "led"), ("B", "button"), ("X", "clock")) if parts[0].startswith(p)), "other"
        )
        uses[gpio] = {"use": use, "label": label, "part": " ".join(parts)}
        if label in signals.get(gpio, {}):
            uses[gpio]["signal"] = label

    # The solder bridges and jumpers and the GPIOs on either side of them
    bridges = []
    for ref, states in sorted(board.states.items()):
        if ref not in board.mounted or ref not in board.pins:
            continue
        reached = [x for pin in board.pins[ref] for x in board.reach(ref, pin, any_state=True)]
        gpios = sorted({pads[p] for r, p, _, _ in reached if is_gpio(r, p)})
        bridges.append(
            {
                "ids": [ref],
                "name": " / ".join(dict.fromkeys(filter(None, (board.label(ref, p) for p in sorted(board.pins[ref]))))),
                "source": "config.json",
                "gpios": gpios,
                "pins": {},
                "states": [
                    {
                        "state": state,
                        "default": state == board.default.get(ref),
                        "text": ", ".join("-".join(p) for p in pairs) if len(states) > 2 else "",
                    }
                    for state, pairs in states.items()
                ],
            }
        )

    return {
        "board": board.name,
        "document": f"{pack.name} v{board.version}".strip(" v"),
        "device": board.device,
        "connectors": connectors,
        "signals": signals,
        "bridges": bridges,
        "uses": dict(sorted(uses.items())),
    }
