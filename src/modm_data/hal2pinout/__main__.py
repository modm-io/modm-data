# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import argparse
import json
import sys
from pathlib import Path

from modm_data.hal2pinout import board_pinout
from modm_data.html2pinout.share import pinout_link
from modm_data.utils import ext_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ext_path("stmicro/cubemx2"), help="The extracted board packs.")
    parser.add_argument("--dfps", type=Path, default=ext_path("stmicro/hal2"), help="The extracted HAL2 DFPs.")
    parser.add_argument("--output", type=Path, default=ext_path("stmicro/pinout/hal2.json"))
    parser.add_argument("--merge", action="store_true", help="Replace the boards of the existing output file.")
    parser.add_argument("--pinout", type=Path, help="The output folder of modm_pinout to link the boards to.")
    args = parser.parse_args()
    known = {}
    if args.merge and args.output.exists():
        known = {b["board"]: b for b in json.loads(args.output.read_text())["boards"]}

    boards = 0
    # Only the board packs of HAL2 have descriptors, the others are just a pdsc
    for pack in sorted(p.parent for p in args.input.glob("*_hw-board/Descriptors")):
        data = board_pinout(pack, args.dfps)
        gpios = {p["signal"] for c in data["connectors"].values() for p in c["pins"].values() if p["kind"] == "gpio"}
        # Like html2pinout, only the signals and bridges of the GPIOs on the connectors
        data["signals"] = {g: s for g, s in data["signals"].items() if g in gpios}
        data["bridges"] = [dict(b, gpios=[g for g in b["gpios"] if g in gpios]) for b in data["bridges"]]
        data["bridges"] = [b for b in data["bridges"] if b["gpios"]]
        # The user manual describes the functions of the pins better than the netlist
        if old := known.get(data["board"]):
            data["document"] += f" + {old['document']}"
            for name, connector in data["connectors"].items():
                old_pins = old["connectors"].get(name, {}).get("pins", {})
                for number, pin in connector["pins"].items():
                    if (o := old_pins.get(str(number))) and o["signal"] == pin["signal"]:
                        pin.update({k: o[k] for k in ("function", "remark") if k in o})
        if args.pinout and (link := pinout_link(args.pinout, data["device"], data["connectors"], data["uses"])):
            data["pinout"] = link
        known[data["board"]] = data
        boards += 1
        sizes = ", ".join(f"{c}={len(p['pins'])}" for c, p in data["connectors"].items())
        print(f"{data['board']:20} {data['device']:16} {sizes}")

    if not boards:
        print(f"No board packs with descriptors found in '{args.input}'!")
        return False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"boards": sorted(known.values(), key=lambda b: b["board"])}, separators=(",", ":")) + "\n"
    )
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
