# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import argparse
import json
import sys
from pathlib import Path

from modm_data.hal2pinout import board_pinout
from modm_data.utils import ext_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ext_path("stmicro/cubemx2"), help="The extracted board packs.")
    parser.add_argument("--dfps", type=Path, default=ext_path("stmicro/hal2"), help="The extracted HAL2 DFPs.")
    parser.add_argument("--output", type=Path, default=ext_path("stmicro/pinout/hal2.json"))
    args = parser.parse_args()

    boards = []
    # Only the board packs of HAL2 have descriptors, the others are just a pdsc
    for pack in sorted(p.parent for p in args.input.glob("*_hw-board/Descriptors")):
        data = board_pinout(pack, args.dfps)
        gpios = {p["signal"] for c in data["connectors"].values() for p in c["pins"].values() if p["kind"] == "gpio"}
        # Like html2pinout, only the signals and bridges of the GPIOs on the connectors
        data["signals"] = {g: s for g, s in data["signals"].items() if g in gpios}
        data["bridges"] = [dict(b, gpios=[g for g in b["gpios"] if g in gpios]) for b in data["bridges"]]
        data["bridges"] = [b for b in data["bridges"] if b["gpios"]]
        boards.append(data)
        sizes = ", ".join(f"{c}={len(p['pins'])}" for c, p in data["connectors"].items())
        print(f"{data['board']:20} {data['device']:16} {sizes}")

    if not boards:
        print(f"No board packs with descriptors found in '{args.input}'!")
        return False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"boards": boards}, separators=(",", ":")) + "\n")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
