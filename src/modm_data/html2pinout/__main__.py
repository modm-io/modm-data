# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
import logging
import argparse
from pathlib import Path

from modm_data.html.document import Document
from modm_data.html2pinout import board_bridges_from_user_manual, board_pinouts_from_user_manual
from modm_data.html2pinout.signals import board_uses, device_for_board, device_signals
from modm_data.utils import ext_path


def _version(path: Path) -> tuple[int, ...]:
    return tuple(int(v) for v in path.name.split("-v")[1].split("_"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, help="A single user manual, otherwise all are converted.")
    parser.add_argument("--input", type=Path, default=ext_path("stmicro/html-archive"))
    parser.add_argument("--output", type=Path, default=ext_path("stmicro/pinout/nucleo.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.document:
        paths = [args.document]
    else:
        # Always choose the latest version
        latest = {}
        for path in args.input.glob("UM*-v*"):
            name = path.name.split("-v")[0]
            if name not in latest or _version(path) > _version(latest[name]):
                latest[name] = path
        paths = sorted(latest.values())

    pinouts = {}
    for path in paths:
        document = Document(path.absolute())
        for board, connectors in board_pinouts_from_user_manual(document).items():
            pinouts[board] = {"board": board, "document": document.fullname, "connectors": connectors}
        for board, bridges in board_bridges_from_user_manual(document).items():
            if board in pinouts:
                pinouts[board]["bridges"] = bridges

    boards = []
    for board, data in sorted(pinouts.items()):
        # Family names like NUCLEO-WB55 describe the same board as NUCLEO-WB55RG
        if any(other != board and other.startswith(board) for other in pinouts):
            continue
        # Only the signals of the GPIOs on the connectors are of interest
        gpios = {p["signal"] for c in data["connectors"].values() for p in c["pins"].values() if p["kind"] == "gpio"}
        device = device_for_board(board)
        signals = device_signals(device[1]) if device else {}
        data["device"] = device[0] if device else None
        data["signals"] = {gpio: signals[gpio] for gpio in sorted(gpios) if gpio in signals}
        # Only the bridges that mention a GPIO on the connectors
        bridges = [dict(b, gpios=[g for g in b["gpios"] if g in gpios]) for b in data.pop("bridges", [])]
        data["bridges"] = [b for b in bridges if b["gpios"]]
        data["uses"] = {gpio: use for gpio, use in board_uses(board).items() if gpio in gpios}
        boards.append(data)
        sizes = ", ".join(f"{c}={len(p['pins'])}" for c, p in data["connectors"].items() if p["type"] == "morpho")
        print(f"{board:20} {data['document']:12} {data['device'] or '-':16} {sizes}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"boards": boards}, separators=(",", ":")) + "\n")
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
