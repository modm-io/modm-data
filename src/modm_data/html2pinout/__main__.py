# Copyright 2026, Niklas Hauser
# SPDX-License-Identifier: MPL-2.0

import json
import logging
import argparse
from pathlib import Path

from modm_data.html.document import Document
from modm_data.html2pinout import (
    board_bridges_from_user_manual,
    board_pinouts_from_user_manual,
    inherit_bridge_defaults,
)
from modm_data.html2pinout.signals import board_uses, device_for_board, device_signals, has_board_file
from modm_data.html2pinout.share import pinout_link
from modm_data.utils import ext_path


def _version(path: Path) -> tuple[int, ...]:
    return tuple(int(v) for v in path.name.split("-v")[1].split("_"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, help="A single user manual, otherwise all are converted.")
    parser.add_argument("--input", type=Path, default=ext_path("stmicro/html-archive"))
    parser.add_argument("--output", type=Path, default=ext_path("stmicro/pinout/nucleo.json"))
    parser.add_argument("--pinout", type=Path, help="The output folder of modm_pinout to link the boards to.")
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

    pinouts, scores = {}, {}
    for path in paths:
        document = Document(path.absolute())
        boards = board_pinouts_from_user_manual(document)
        for board, connectors in boards.items():
            # Several manuals may name a board, the one that describes the most of it is its own. The manuals
            # of the Discovery kits however also name their predecessors, which have their own smaller manual.
            score = (board.startswith("NUCLEO") or -len(boards), sum(len(c["pins"]) for c in connectors.values()))
            if board in pinouts and score <= scores[board]:
                continue
            scores[board] = score
            pinouts[board] = {"board": board, "document": document.fullname, "connectors": connectors}
        bridges = board_bridges_from_user_manual(document)
        # Older revisions of the manual may still know the defaults that this one lost
        older = sorted(args.input.glob(f"{document.name}-v*"), key=_version, reverse=True)
        for old in older if not args.document else []:
            missing = any(not any(o["default"] for o in b["states"]) for bs in bridges.values() for b in bs)
            if missing and old != path:
                inherit_bridge_defaults(bridges, board_bridges_from_user_manual(Document(old.absolute())), old.name)
        for board, bridges in bridges.items():
            if pinouts.get(board, {}).get("document") == document.fullname:
                pinouts[board]["bridges"] = bridges

    boards = []
    for board, data in sorted(pinouts.items()):
        # Family names like NUCLEO-WB55 or NUCLEO-WL55JC describe the same board as NUCLEO-WB55RG or
        # NUCLEO-WL55JC1. A real board like NUCLEO-L452RE next to NUCLEO-L452RE-P has its own description.
        if any(other != board and other.startswith(board) for other in pinouts) and not has_board_file(board):
            continue
        # The Discovery kits have marketing names and placeholders next to their real name
        if not board.startswith("NUCLEO") and not has_board_file(board):
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
        uses = board_uses(board)
        data["uses"] = {gpio: use for gpio, use in uses.items() if gpio in gpios}
        if device and args.pinout and (link := pinout_link(args.pinout, device[0], data["connectors"], uses)):
            data["pinout"] = link
        boards.append(data)
        sizes = ", ".join(
            f"{c}={len(p['pins'])}" for c, p in data["connectors"].items() if p["type"] in {"morpho", "extension"}
        )
        print(f"{board:20} {data['document']:12} {data['device'] or '-':16} {sizes}")

    if not boards:
        print(f"No user manuals with board pinouts found in '{args.input}'!")
        return False

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"boards": boards}, separators=(",", ":")) + "\n")
    return True


if __name__ == "__main__":
    exit(0 if main() else 1)
